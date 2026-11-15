"""Build tract needs and open-ground context from the supplied project snapshots.

No source is downloaded. Building context is optional; cached simulator footprint
pages are used when complete, and missing context is explicitly labeled.
"""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from math import isfinite
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .geometry import polygon_model, project, unproject, validate_wgs84
from .planning_schemas import AreaNeed, AreaProperties, PlanningArea, PlanningIndex, PlanningSources, SiteContext
from .release import ROOT
from .schemas import CATEGORIES, ParcelFeature, ServiceFeature
from .spatial import SpatialScoring

TRACT_URL = "https://drive.google.com/file/d/1X2oVuTAZH7kmUtnynN-LVVt4R1kZNbYx/view"
BUILDINGS_URL = "https://data.sfgov.org/d/ynuv-fyni"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_tracts(path):
    data = json.loads(path.read_text())
    crs = data.get("crs", {}).get("properties", {}).get("name", "EPSG:4326")
    if crs not in {"EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:EPSG::4326"}:
        raise ValueError("Tract input must use WGS84 longitude/latitude")
    features, ids = [], set()
    for feature in data["features"]:
        props = feature["properties"]
        geoid = str(props["geoid"])
        if len(geoid) != 11 or not geoid.isdigit() or not geoid.startswith("06075") or geoid in ids:
            raise ValueError("Expected unique, eleven-digit San Francisco tract GEOIDs")
        geometry = validate_wgs84(shape(feature["geometry"]))
        polygon_model(geometry)
        ids.add(geoid)
        features.append((geoid, props, geometry))
    if not features:
        raise ValueError("No tract boundaries supplied")
    return sorted(features, key=lambda row: row[0])


def assign_area(tree, tracts, coordinates):
    matches = tree.query(Point(coordinates), predicate="intersects")
    # A point on a shared boundary belongs to one tract, deterministically.
    return min((tracts[int(i)][0] for i in matches), default=None)


def summarize_area_needs(people, spatial, assignments, target_values):
    grouped = defaultdict(list)
    for i, area_id in enumerate(assignments):
        if area_id is not None:
            grouped[area_id].append(i)
    result = {}
    for area_id, indexes in grouped.items():
        indexes = np.asarray(indexes, dtype=int)
        supported = indexes[spatial.supported[indexes]]
        population = float(spatial.people[indexes].sum())
        supported_population = float(spatial.people[supported].sum())
        needs = []
        for category in CATEGORIES:
            target = target_values[category]
            deficit = None
            reason = None
            if not supported_population:
                reason = "no_supported_population"
            elif target is None or target <= 0:
                reason = "no_positive_accessibility_target"
            else:
                gaps = np.maximum(0, 1 - spatial.baselines[category].accessibility[supported] / target)
                deficit = float(np.average(gaps, weights=spatial.people[supported]) * 100)
            needs.append(AreaNeed(category_id=category, access_deficit_pct=deficit,
                                  covered_population=supported_population, reason=reason))
        result[area_id] = (population, supported_population, needs)
    return result


def building_context(cache, parcels):
    paths = [cache / f"footprints_{off}.geojson" for off in (0, 50000, 100000, 150000)]
    complete = all(path.is_file() for path in paths)
    footprints, heights, bad_lots = defaultdict(list), {}, set()
    if complete:
        for path in paths:
            with path.open() as stream:
                features = json.load(stream)["features"]
            for record in features:
                props = record.get("properties") or {}
                lot = props.get("mblr") or ""
                parcel_id = f"sf:{lot[2:]}" if lot.startswith("SF") else ""
                if parcel_id not in parcels:
                    continue
                geometry = record.get("geometry")
                if not geometry or not shape(geometry).is_valid:
                    bad_lots.add(parcel_id)
                    continue
                footprints[parcel_id].append(project(shape(geometry)))
                height = float(props.get("hgt_median_m") or 0)
                if isfinite(height) and height >= 0:
                    heights[parcel_id] = max(heights.get(parcel_id, 0), height)
    return complete, footprints, heights, bad_lots


def build_index(tract_path, input_dir, release_path, footprint_cache):
    tracts = load_tracts(tract_path)
    tree = STRtree([row[2] for row in tracts])
    with release_path.open() as stream:
        frozen = json.load(stream)
    manifest = json.loads((input_dir / "backend_manifest.json").read_text())
    source_manifest = json.loads((input_dir / "source_manifest.json").read_text())
    data_version = frozen["versions"]["data_version"]
    if frozen["mode"] != "live" or data_version != manifest["data_version"] or data_version != source_manifest["data_version"]:
        raise ValueError("Canonical inputs and frozen live release must have the same data version")
    for name in ("parcels.geoparquet", "demographics.geoparquet"):
        if digest(input_dir / name) != source_manifest["outputs"][name]["sha256"]:
            raise ValueError(f"Published checksum does not match {name}")
    eligible = set(frozen["comparisons"])
    parcels = {
        row["id"]: ParcelFeature.model_validate(row) for row in frozen.pop("parcels")
        if row["id"] in eligible and row["properties"]["vacancy_status"] == "source_indicated_vacant"
    }
    # Explicitly recheck the water flag in the canonical snapshot.
    flags = pq.read_table(input_dir / "parcels.geoparquet", columns=["parcel_id", "submerged"]).to_pylist()
    water = {row["parcel_id"] for row in flags if row["submerged"]}
    if water & parcels.keys():
        raise ValueError("Submerged parcels cannot enter the vacant-ground index")
    services = [ServiceFeature.model_validate(row) for row in frozen.pop("services")]
    targets = frozen["need_targets"]
    del frozen
    columns = ["geoid", "geo_level", "population", "rep_lon", "rep_lat", "acs_vintage"]
    rows = pq.read_table(input_dir / "demographics.geoparquet", columns=columns).to_pylist()
    if len({row["geoid"] for row in rows}) != len(rows) or any(row["geo_level"] != "block_group" for row in rows):
        raise ValueError("Population input must contain unique block groups, without tract-total duplicates")
    rows = sorted((row for row in rows if row["population"] is not None), key=lambda row: row["geoid"])
    people = [((row["rep_lon"], row["rep_lat"]), row["population"]) for row in rows]
    coverage = manifest.get("spatial_coverage")
    spatial = SpatialScoring(people, services, shape(coverage) if coverage else None)
    assignments = [assign_area(tree, tracts, coordinate) if row["geoid"].startswith("06075") else None
                   for row, (coordinate, _) in zip(rows, people)]
    needs = summarize_area_needs(people, spatial, assignments, targets)
    complete, footprints, heights, bad_lots = building_context(footprint_cache, parcels)
    sites = []
    for parcel_id, parcel in sorted(parcels.items()):
        geometry = project(shape(parcel.geometry.model_dump()))
        known = complete and parcel_id not in bad_lots
        mapped = footprints.get(parcel_id, [])
        available = geometry.difference(unary_union(mapped)) if known and mapped else geometry
        if available.geom_type == "GeometryCollection":
            available = unary_union([part for part in available.geoms if part.geom_type in {"Polygon", "MultiPolygon"}])
        sites.append(SiteContext(
            parcel_id=parcel_id,
            area_id=assign_area(tree, tracts, parcel.properties.representative_point.coordinates),
            available_geometry=polygon_model(unproject(available)) if not available.is_empty else None,
            buildings_checked=known, existing_building_count=len(mapped) if known else 0,
            existing_height_m=heights.get(parcel_id) if known else None,
        ))
    counts = Counter(site.area_id for site in sites)
    areas = []
    for geoid, props, geometry in tracts:
        empty_needs = [AreaNeed(category_id=c, access_deficit_pct=None, covered_population=0,
                                reason="no_supported_population") for c in CATEGORIES]
        population, supported, area_needs = needs.get(geoid, (0, 0, empty_needs))
        center = geometry.representative_point()
        areas.append(PlanningArea(id=geoid, geometry=polygon_model(geometry), properties=AreaProperties(
            name=props.get("namelsad") or f"Census Tract {props.get('name', geoid)}",
            population=population, supported_population=supported, candidate_count=counts[geoid],
            representative_point=(center.x, center.y), needs=area_needs,
        )))
    return PlanningIndex(
        data_version=data_version, need_targets=targets, areas=areas, sites=sites,
        unassigned_population_points=sum(row["geoid"].startswith("06075") and area is None
                                         for row, area in zip(rows, assignments)),
        sources=PlanningSources(
            tract_url=TRACT_URL, tract_sha256=digest(tract_path),
            tract_vintage=min(str(props.get("data_as_of", "Unspecified"))[:10] for _, props, _ in tracts),
            population_vintage="; ".join(sorted({row["acs_vintage"] for row in rows})),
            building_source=BUILDINGS_URL if complete else None,
            demographic_sha256=digest(input_dir / "demographics.geoparquet"),
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracts", type=Path, default=ROOT / "config/geography/sf-tracts.geojson",
                        help="Tract GeoJSON (defaults to the boundary file included in the repository)")
    parser.add_argument("--input", type=Path, default=ROOT / "artifacts/person2")
    parser.add_argument("--release", type=Path, default=ROOT / "artifacts/release.json")
    parser.add_argument("--footprint-cache", type=Path, default=ROOT / "simulator/pipeline/cache")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/planning/index.json")
    args = parser.parse_args()
    try:
        index = build_index(args.tracts, args.input, args.release, args.footprint_cache)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(index.model_dump_json())
        temporary.replace(args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    unassigned = sum(site.area_id is None for site in index.sites)
    print(f"Built {len(index.areas)} tract needs and {len(index.sites)} vacant-site contexts; {unassigned} parcels outside supplied boundaries.")


if __name__ == "__main__":
    main()
