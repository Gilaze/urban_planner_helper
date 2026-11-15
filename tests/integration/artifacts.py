"""Validate exported data without importing pipeline or backend code.

JSON row arrays are accepted for tiny checker fixtures. Real releases must use
the Parquet/GeoParquet outputs named in the implementation plan.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .oracle import CANDIDATES, CATEGORIES
from .reporting import Report

REQUIRED = {
    "parcels": {"parcel_id", "source_apns", "geometry", "representative_point", "area_m2",
                "existing_use", "vacancy_status", "zoning", "source_vintage"},
    "places": {"place_id", "longitude", "latitude", "city", "primary_category", "service_tags",
               "closure_status", "reviews_count"},
    "review_summary": {"place_id", "sampled_review_count", "mean_rating", "valid_date_start", "valid_date_end"},
    "demographics": {"geoid", "geometry", "representative_point", "population", "land_area_m2",
                     "median_household_income_usd", "median_gross_rent_usd", "source_vintage",
                     "population_moe", "income_moe", "rent_moe"},
    "place_features": {"place_id", "category_id", "target", "split"},
    "parcel_features": {"parcel_id", "category_id"},
}
KEYS = {"parcels": ("parcel_id",), "places": ("place_id",), "review_summary": ("place_id",),
        "demographics": ("geoid",), "place_features": ("place_id",),
        "parcel_features": ("parcel_id", "category_id")}
PRIVATE = {"reviewer_id", "reviewer_name", "author_name", "user_id", "review_text", "raw_text", "text"}


def finite(value, *, low=None, high=None, nullable=False):
    if value is None:
        return nullable
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            and (low is None or value >= low) and (high is None or value <= high))


def read_json(path):
    def reject(value):
        raise ValueError(f"Non-JSON constant {value}")
    return json.loads(Path(path).read_text(), parse_constant=reject)


def _read_table(path):
    if path.suffix == ".json":
        rows = read_json(path)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("JSON table must be an array of row objects")
        return rows, {}
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ValueError("Install tests/integration/requirements.txt to inspect Parquet") from exc
    table = pq.read_table(path)
    geo = json.loads((table.schema.metadata or {}).get(b"geo", b"{}"))
    return table.to_pylist(), geo


def _date(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _xy_points(geometry):
    if not isinstance(geometry, dict) or "coordinates" not in geometry:
        raise ValueError("Expected GeoJSON geometry")
    coordinates = geometry["coordinates"]
    def walk(value):
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("Empty/malformed coordinates")
        if isinstance(value[0], (int, float)):
            if len(value) < 2 or not all(finite(x) for x in value[:2]):
                raise ValueError("Nonfinite coordinates")
            yield value[:2]
        else:
            for part in value:
                yield from walk(part)
    return list(walk(coordinates))


def _geometry(value, crs, *, polygon):
    try:
        from pyproj import CRS, Transformer
        from shapely import wkb
        from shapely.geometry import shape, mapping
        from shapely.ops import transform
    except ImportError as exc:
        raise ValueError("Install geometry dependencies from requirements.txt") from exc
    geom = wkb.loads(bytes(value)) if isinstance(value, (bytes, bytearray)) else shape(value)
    if geom.is_empty or not geom.is_valid:
        raise ValueError("Empty or invalid geometry")
    if polygon and geom.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Parcel/demographic geometry must be a polygon")
    if not polygon and geom.geom_type != "Point":
        raise ValueError("Representative geometry must be a point")
    source_crs = CRS.from_user_input(crs)
    if source_crs.to_epsg() not in {4326, 26910}:
        raise ValueError("Expected EPSG:26910 or EPSG:4326")
    wgs = transform(Transformer.from_crs(source_crs, 4326, always_xy=True).transform, geom)
    if any(not -180 <= x <= 180 or not -90 <= y <= 90 for x, y in _xy_points(mapping(wgs))):
        raise ValueError("Coordinates outside WGS84 bounds")
    # Bay Area release coverage catches swapped axes and mislabeled projected data.
    if any(not -123.5 <= x <= -121 or not 36.5 <= y <= 39 for x, y in _xy_points(mapping(wgs))):
        raise ValueError("Geometry lies outside the Bay Area release envelope")
    metric = transform(Transformer.from_crs(source_crs, 26910, always_xy=True).transform, geom)
    return geom, metric


def validate_artifacts(directory, report: Report):
    directory = Path(directory)
    reproduction = f"python -m tests.integration.verify_release --artifact-dir '{directory}'"
    tables = {}
    manifest_path = directory / "source_manifest.json"
    try:
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be an object")
        for field in ("pipeline_version", "sources", "artifacts", "units", "coverage"):
            if not manifest.get(field):
                raise ValueError(f"Missing manifest {field}")
        if not isinstance(manifest["sources"], list) or any(not isinstance(manifest[k], dict) for k in ("artifacts", "units", "coverage")):
            raise ValueError("Manifest sources must be an array; artifacts, units and coverage must be objects")
        if not isinstance(manifest.get("fixture_mode"), bool):
            raise ValueError("Manifest must explicitly label fixture_mode")
        if report.mode == "release" and manifest["fixture_mode"]:
            raise ValueError("Synthetic artifacts cannot pass a live release")
        for source in manifest["sources"]:
            if not source.get("url", "").startswith("https://") or not source.get("vintage"):
                raise ValueError("Each source needs HTTPS attribution and vintage")
            if _date(source["retrieved_at"]).tzinfo is None:
                raise ValueError("Source retrieval timestamp needs a timezone")
            if not re.fullmatch(r"[0-9a-f]{64}", source["checksum"]):
                raise ValueError("Source checksum must be SHA256")
            if not isinstance(source.get("counts"), dict):
                raise ValueError("Each source needs record counts")
        expected_units = {"area": "m2", "distance": "m", "retained_tax": "USD/year", "jobs": "operating FTE"}
        if any(manifest["units"].get(k) != v for k, v in expected_units.items()):
            raise ValueError(f"Units must state {expected_units}")
        report.add("artifacts.manifest", "PASS", "Data", "Source attribution, vintage, units and fixture labeling present.", reproduction,
                   "Versioned source manifest with checksums and explicit units.", impact="provenance")
    except Exception as exc:
        report.add("artifacts.manifest", "FAIL", "Data", exc, reproduction, "Readable, complete source_manifest.json.")
        manifest = {}

    for name, required in REQUIRED.items():
        paths = [directory / f"{name}{ext}" for ext in (".geoparquet", ".parquet", ".json")]
        path = next((p for p in paths if p.is_file()), None)
        if path is None:
            report.add(f"artifacts.{name}", "BLOCKED", "Data", f"No {name} export found.", reproduction,
                       "Canonical table from the data workstream.", categories=CANDIDATES)
            continue
        try:
            if path.suffix == ".json" and report.mode == "release":
                raise ValueError("JSON tables are checker fixtures; release artifacts must be Parquet")
            rows, geo = _read_table(path)
            if not rows:
                raise ValueError("Table is empty")
            if any(required - row.keys() for row in rows):
                missing = sorted(set().union(*(required - row.keys() for row in rows)))
                raise ValueError(f"Missing expected columns: {missing}")
            keys = [tuple(row[k] for k in KEYS[name]) for row in rows]
            if any(any(not isinstance(v, str) or not v for v in key) for key in keys):
                raise ValueError("Primary keys must be nonempty strings")
            if len(set(keys)) != len(keys):
                raise ValueError(f"Duplicate keys at grain {KEYS[name]}")
            if any(PRIVATE & row.keys() for row in rows):
                raise ValueError("Reviewer identity or raw review text in release output")
            record = manifest.get("artifacts", {}).get(name, {})
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if record.get("sha256") != digest or record.get("row_count") != len(rows) or record.get("file") != path.name:
                raise ValueError("Artifact filename, SHA256, or row count differs from manifest")
            if name in {"parcels", "demographics"}:
                geometry_column = geo.get("columns", {}).get("geometry", {})
                # GeoParquet's omitted CRS means OGC:CRS84, with longitude first.
                crs = geometry_column.get("crs", "EPSG:4326") if geo else manifest.get("geometry_crs", {}).get(name)
                if crs is None:
                    raise ValueError("Geometry CRS must be declared")
                if path.suffix != ".json" and not geo:
                    raise ValueError("GeoParquet metadata is required for geographic tables")
                for row in rows:
                    polygon, metric = _geometry(row["geometry"], crs, polygon=True)
                    point, _ = _geometry(row["representative_point"], crs, polygon=False)
                    if not polygon.covers(point):
                        raise ValueError("Representative point is outside its polygon")
                    if not row["source_vintage"]:
                        raise ValueError("Missing source vintage")
                    if name == "parcels":
                        if not row["parcel_id"].startswith("sf:"):
                            raise ValueError("Parcel ID must use sf:<physical-lot-key>")
                        if not finite(row["area_m2"], low=0) or row["area_m2"] == 0:
                            raise ValueError("Parcel area must be positive m2")
                        if abs(metric.area - row["area_m2"]) > max(1, metric.area * 0.02):
                            raise ValueError("Parcel area differs from EPSG:26910 geometry by >2%/1m2")
                        apns = row["source_apns"]
                        if not isinstance(apns, list) or not apns or any(not isinstance(v, str) or not v for v in apns):
                            raise ValueError("APNs must be nonempty strings, preserving leading zeros")
                        if len(set(apns)) != len(apns):
                            raise ValueError("Duplicate APN within a physical parcel")
                        if row["vacancy_status"] not in {"source_indicated_vacant", "unknown", "not_vacant"}:
                            raise ValueError("Invalid vacancy status")
                        if row["vacancy_status"] == "source_indicated_vacant" and not row.get("vacancy_evidence"):
                            raise ValueError("Vacant status needs explicit source evidence")
                        if row["vacancy_status"] != "source_indicated_vacant" and row.get("hypothetical") is not True:
                            raise ValueError("Scoring site without vacancy evidence must be hypothetical")
                    else:
                        if not re.fullmatch(r"\d{11,12}", row["geoid"]):
                            raise ValueError("Tract/block-group GEOID must be an 11/12-digit string")
                        for col in ("population", "land_area_m2", "median_household_income_usd", "median_gross_rent_usd",
                                    "population_moe", "income_moe", "rent_moe"):
                            if not finite(row[col], low=0, nullable=col != "population"):
                                raise ValueError(f"Invalid demographic {col}; missing estimates must be null")
                        if row["land_area_m2"] in (None, 0):
                            raise ValueError("Density requires positive Census land area")
            if name == "places":
                for row in rows:
                    if row["city"] not in {"Oakland", "Berkeley", "San Francisco"}:
                        raise ValueError("City must be Oakland, Berkeley or San Francisco")
                    if not finite(row["longitude"], low=-123.5, high=-121) or not finite(row["latitude"], low=36.5, high=39):
                        raise ValueError("Invalid or swapped Bay Area coordinates")
                    if row["primary_category"] not in CATEGORIES:
                        raise ValueError("Unaccepted primary category")
                    tags = row["service_tags"]
                    if not isinstance(tags, list) or any(t not in CATEGORIES for t in tags) or len(tags) != len(set(tags)):
                        raise ValueError("Supply tags must be accepted and deduplicated")
                    if row["closure_status"] not in {"open", "unknown", "temporarily_closed", "permanently_closed"}:
                        raise ValueError("Unknown closure status")
                    count = row["reviews_count"]
                    if count is not None and (not finite(count, low=0) or int(count) != count):
                        raise ValueError("Reviews count must be a nonnegative integer or null")
            if name == "review_summary":
                for row in rows:
                    count = row["sampled_review_count"]
                    if not finite(count, low=0) or int(count) != count or not finite(row["mean_rating"], low=1, high=5, nullable=True):
                        raise ValueError("Invalid sampled review count/rating")
                    start, end = row["valid_date_start"], row["valid_date_end"]
                    if (start is None) != (end is None) or (start is not None and _date(start) > _date(end)):
                        raise ValueError("Invalid review date range")
            if name.endswith("features"):
                if any(r["category_id"] not in (CATEGORIES if name == "place_features" else CANDIDATES) for r in rows):
                    raise ValueError("Unaccepted feature category")
                if name == "parcel_features" and any({"target", "split"} & r.keys() for r in rows):
                    raise ValueError("Training target or split leaked into parcel features")
            tables[name] = rows
            missing = {c: sum(r.get(c) is None for r in rows) for c in sorted(required)}
            report.add(f"artifacts.{name}", "PASS", "Data", f"{len(rows)} rows; unique grain {KEYS[name]}; null counts {missing}.",
                       reproduction, "Valid columns, types, keys, coordinates and provenance.", impact="data integrity")
        except Exception as exc:
            report.add(f"artifacts.{name}", "FAIL", "Data", exc, reproduction,
                       "Canonical records with valid keys, geometry and manifest fingerprints.", categories=CANDIDATES)

    if "parcels" in tables:
        apns = [apn for row in tables["parcels"] for apn in row["source_apns"]]
        duplicate = [key for key, n in Counter(apns).items() if n > 1]
        report.add("joins.physical_parcels", "FAIL" if duplicate else "PASS", "Data",
                   f"APNs assigned to multiple physical parcels: {duplicate}", reproduction,
                   "One physical-lot mapping per source APN.", impact="join cardinality")
    if {"places", "review_summary"} <= tables.keys():
        place_ids = {r["place_id"] for r in tables["places"]}
        review_ids = {r["place_id"] for r in tables["review_summary"]}
        unmatched = sorted(review_ids - place_ids)
        report.add("joins.places_reviews", "WARN" if unmatched else "PASS", "Data",
                   f"Left join retains {len(place_ids)} Places rows; {len(unmatched)} unmatched review IDs; "
                   f"{len(place_ids - review_ids)} Places rows have no sampled reviews.", reproduction,
                   "Review summary is unique before left join; unmatched IDs reported.", impact="coverage")
    if {"places", "place_features"} <= tables.keys():
        places = {r["place_id"]: r for r in tables["places"]}
        bad = [r["place_id"] for r in tables["place_features"] if r["place_id"] not in places
               or r["category_id"] != places[r["place_id"]]["primary_category"]]
        report.add("joins.training_places", "FAIL" if bad else "PASS", "Data", f"Unmatched or recategorized feature IDs: {bad[:20]}",
                   reproduction, "One feature row maps to one deduplicated Places row.", impact="join cardinality")
    if {"parcels", "parcel_features"} <= tables.keys():
        expected = {(r["parcel_id"], c) for r in tables["parcels"] for c in CANDIDATES}
        actual = {(r["parcel_id"], r["category_id"]) for r in tables["parcel_features"]}
        report.add("joins.parcel_scenarios", "PASS" if expected == actual else "FAIL", "Data",
                   f"Missing {len(expected - actual)} scenarios; orphan {len(actual - expected)} scenarios.", reproduction,
                   "Exactly one row per physical parcel and candidate, including unavailable metrics.", impact="comparison coverage")
    coverage = manifest.get("coverage", {})
    if "places" in tables:
        observed = Counter((r["city"], r["primary_category"]) for r in tables["places"])
        counts = {f"{city}:{cat}": n for (city, cat), n in sorted(observed.items())}
        overlap = sum(len(r["service_tags"]) > 1 for r in tables["places"])
        closures = dict(Counter(r["closure_status"] for r in tables["places"]))
        reported = coverage.get("places_by_city_category")
        report.add("coverage.supply", "PASS" if reported == counts and coverage.get("closure_counts") == closures else "FAIL", "Data",
                   f"Observed city/category counts: {counts}; closures: {closures}; {overlap} multi-tag places.", reproduction,
                   "Manifest city/category and closure counts match canonical Places.", impact="coverage")
    report.add("coverage.boundaries", "PASS" if finite(coverage.get("population_service_buffer_m"), low=1600)
               and coverage.get("city_edges_preserved") is True else "FAIL", "Data",
               f"Boundary declaration: {coverage.get('population_service_buffer_m')} m; "
               f"city edges preserved: {coverage.get('city_edges_preserved')}. This checks the declaration; spatial spot checks are separate.", reproduction,
               "Population/service coverage includes both 800 m 2SFCA steps at city edges.", impact="coverage")
    return tables
