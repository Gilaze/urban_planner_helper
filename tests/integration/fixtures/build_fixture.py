"""Regenerate tiny synthetic checker inputs; never downloads real data.

Run from the repository root with the pinned verification environment:
  python -m tests.integration.fixtures.build_fixture
"""

import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from ..oracle import CANDIDATES, DIMENSIONS
from ..model_audit import PREDICTORS

HERE = Path(__file__).parent
DATA = HERE / "artifacts"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def main():
    project = Transformer.from_crs(4326, 26910, always_xy=True).transform
    polygon = {"type": "Polygon", "coordinates": [[[-122.42, 37.775], [-122.4198, 37.775],
                [-122.4198, 37.7752], [-122.42, 37.7752], [-122.42, 37.775]]]}
    point = {"type": "Point", "coordinates": [-122.4199, 37.7751]}
    parcel = {"parcel_id": "sf:checker-0001", "source_apns": ["0001001"], "geometry": polygon,
              "representative_point": point, "area_m2": transform(project, shape(polygon)).area,
              "existing_use": "unknown", "vacancy_status": "unknown", "vacancy_evidence": None,
              "hypothetical": True, "zoning": None, "source_vintage": "synthetic-2026", "geometry_hash": "checker-geometry-v1"}
    tables = {"parcels": [parcel], "places": [], "review_summary": [], "place_features": [], "parcel_features": [],
              "demographics": [{"geoid": "060750101001", "geometry": polygon, "representative_point": point,
                  "population": 300, "land_area_m2": 10000, "median_household_income_usd": None,
                  "median_gross_rent_usd": 2000, "source_vintage": "synthetic-2026", "population_moe": 10,
                  "income_moe": None, "rent_moe": 50}]}
    cities = [("Oakland", -122.27, 37.80, "a"), ("Oakland", -122.2701, 37.8001, "b"),
              ("Berkeley", -122.27, 37.87, "v"), ("San Francisco", -122.419, 37.775, "t")]
    for city, lon, lat, suffix in cities:
        for i, category in enumerate(CANDIDATES):
            place_id = f"{category}-{suffix}"
            tables["places"].append({"place_id": place_id, "longitude": lon + i * .001, "latitude": lat,
                "city": city, "primary_category": category, "service_tags": [category, "hospital"] if i == 0 else [category],
                "closure_status": "open", "reviews_count": 10 + i * 5 + {"a": 0, "b": 10, "v": 5, "t": 20}[suffix]})
            if suffix != "t":
                tables["review_summary"].append({"place_id": place_id, "sampled_review_count": 2,
                    "mean_rating": 4.0, "valid_date_start": "2024-01-01", "valid_date_end": "2025-01-01"})
    places = tables["places"]
    xy = {r["place_id"]: project(r["longitude"], r["latitude"]) for r in places}
    for i, place in enumerate(places):
        distances = [(p, math.dist(xy[place["place_id"]], xy[p["place_id"]])) for p in places if p["place_id"] != place["place_id"]]
        peer = lambda p: place["primary_category"] in {p["primary_category"], *p["service_tags"]}
        values = {}
        for radius in (400, 800, 1600):
            values[f"peer_count_{radius}m"] = sum(d <= radius and peer(p) for p, d in distances)
            values[f"other_service_count_{radius}m"] = sum(d <= radius and not peer(p) for p, d in distances)
        values.update(transit_count_400m=0, transit_count_800m=0,
            nearest_peer_m=min((d for p, d in distances if peer(p)), default=None),
            population_density_per_km2=1000 + i * 100, income_usd=40000 + i * 1000, rent_usd=2000,
            category_id=place["primary_category"], density_missing=False, income_missing=False, rent_missing=False)
        tables["place_features"].append({"place_id": place["place_id"], **{name: values[name] for name in PREDICTORS},
            "target": math.log1p(place["reviews_count"]), "split": {"Oakland": "train", "Berkeley": "validation", "San Francisco": "test"}[place["city"]]})
    for category in CANDIDATES:
        sample = next(r for r in tables["place_features"] if r["category_id"] == category)
        values = {name: sample[name] for name in PREDICTORS}
        distances = [(p, math.dist(project(*point["coordinates"]), xy[p["place_id"]])) for p in places]
        peer = lambda p: category in {p["primary_category"], *p["service_tags"]}
        for radius in (400, 800, 1600):
            values[f"peer_count_{radius}m"] = sum(d <= radius and peer(p) for p, d in distances)
            values[f"other_service_count_{radius}m"] = sum(d <= radius and not peer(p) for p, d in distances)
        values.update(nearest_peer_m=min((d for p, d in distances if peer(p)), default=None),
                      population_density_per_km2=30000, income_usd=None, income_missing=True)
        tables["parcel_features"].append({"parcel_id": parcel["parcel_id"], **{name: values[name] for name in PREDICTORS}})
    for name, rows in tables.items():
        save(DATA / f"{name}.json", rows)
    save(DATA / "feature_definitions.json", {"feature_names": PREDICTORS, "distance_crs": "EPSG:26910",
        "note": "Synthetic checker inputs, not a real spatial release."})
    model = {"model_attempted": False, "selected_approach": "category_baseline", "supported_model_categories": [],
             "seed": 42, "estimator_version": "stdlib-baseline-checker-v1", "sf_evaluation_count": 1,
             "fit_place_ids": {}, "normalization": {}, "slice_thresholds": {}}
    features = {r["place_id"]: r for r in tables["place_features"]}
    predictions = []
    for stage, fit_cities, eval_city in (("selection", {"Oakland"}, "Berkeley"), ("final", {"Oakland", "Berkeley"}, "San Francisco")):
        fit = [r for r in places if r["city"] in fit_cities]
        model["fit_place_ids"][stage] = [r["place_id"] for r in fit]
        model["normalization"][stage] = {}
        model["slice_thresholds"][stage] = {field: statistics.median(features[r["place_id"]][field] for r in fit)
            for field in ("income_usd", "population_density_per_km2")}
        for cat in CANDIDATES:
            values = [math.log1p(r["reviews_count"]) for r in fit if r["primary_category"] == cat]
            model["normalization"][stage][cat] = {"mean": statistics.mean(values), "std": statistics.pstdev(values) or 1}
            for place in (r for r in places if r["city"] == eval_city and r["primary_category"] == cat):
                for approach in ("category_baseline", "category_city_baseline"):
                    predictions.append({"stage": stage, "approach": approach, "place_id": place["place_id"],
                        "category_id": cat, "city": eval_city, "actual_log_count": math.log1p(place["reviews_count"]),
                        "prediction_log_count": statistics.median(values)})
    save(DATA / "model_manifest.json", model)
    save(DATA / "predictions.json", predictions)
    from ..oracle import empirical_percentile
    reference_rows = [{"parcel_id": parcel["parcel_id"], "category_id": cat, "raw": {
        "need": [0, 10, 20, 20, 30][i], "retained_tax_usd_per_year": 0,
        "operating_fte": i + 1 if i < 4 else None, "sustainability": i * 100}} for i, cat in enumerate(CANDIDATES)]
    refs = {metric: [r["raw"][metric] for r in reference_rows if r["raw"][metric] is not None]
            for metric in ("need", "retained_tax_usd_per_year", "operating_fte", "sustainability")}
    for row in reference_rows:
        scores = {k: empirical_percentile(v, refs[k]) if v is not None else None for k, v in row["raw"].items()}
        row["normalized"] = {"need": scores["need"], "sustainability": scores["sustainability"], "economic":
            (scores["retained_tax_usd_per_year"] + scores["operating_fte"]) / 2 if scores["operating_fte"] is not None else None}
    save(DATA / "scoring_reference.json", {"method": "midpoint_ecdf", "data_version": "checker-synthetic-v1",
                                         "references": refs, "rows": reference_rows})
    artifacts = {name: {"file": f"{name}.json", "row_count": len(rows),
        "sha256": hashlib.sha256((DATA / f"{name}.json").read_bytes()).hexdigest()} for name, rows in tables.items()}
    save(DATA / "source_manifest.json", {"pipeline_version": "checker-synthetic-v1", "fixture_mode": True,
        "sources": [{"url": "https://github.com/Gilaze/urban_planner_helper/blob/main/IMPLEMENTATION_PLAN.md",
            "retrieved_at": "2026-11-15T00:00:00+00:00", "vintage": "synthetic-2026",
            "checksum": hashlib.sha256(b"synthetic checker source; no observed data").hexdigest(), "counts": {"places": len(places)}}],
        "artifacts": artifacts, "geometry_crs": {"parcels": "EPSG:4326", "demographics": "EPSG:4326"},
        "units": {"area": "m2", "distance": "m", "retained_tax": "USD/year", "jobs": "operating FTE"},
        "coverage": {"population_service_buffer_m": 1600, "city_edges_preserved": True,
            "places_by_city_category": {f"{city}:{cat}": n for (city, cat), n in sorted(Counter((r["city"], r["primary_category"]) for r in places).items())},
            "closure_counts": {"open": len(places)}, "note": "Synthetic declaration; real spatial coverage is not established."}})
    save(HERE / "oracle_cases.json", [{"parcel_id": parcel["parcel_id"], "category_id": "clinic", "populations": [100, 200],
        "existing_distances_m": [[400], [800]], "proposed_distances_m": [800, 400], "baseline_distances_m": [400, 800]}])
    # Literal hand-calculated raw values; no backend/scoring imports here.
    from ..oracle import rank
    normalized = {cat: {"need": [30, 70, 50, 20, 90][i], "economic": None if cat == "school" else 50,
                         "sustainability": [80, 20, 60, 10, 40][i]} for i, cat in enumerate(CANDIDATES)}
    ranking = rank(normalized)
    rows = []
    footprint = {"type": "Polygon", "coordinates": [[[-122.42, 37.775], [-122.4199, 37.775],
                  [-122.4199, 37.7752], [-122.42, 37.7752], [-122.42, 37.775]]]}
    for cat in CANDIDATES:
        scored = next(r for r in ranking["alternatives"] if r["category_id"] == cat)
        rows.append({**scored, "normalized": normalized[cat], "raw": {
            "need": {"deficit_before": 2 / 9, "deficit_after": 0, "benefit": 2 / 9, "covered_population": 300},
            "economic": {"retained_tax_usd_per_year": {"p10": 0, "p50": 0, "p90": 0}, "operating_fte": None if cat == "school" else {"p10": 1, "p50": 2, "p90": 3}},
            "sustainability": {"before_m": 2000 / 3, "after_m": 400, "saved_m": 800 / 3, "baseline_coverage": 1}},
            "coverage": {"baseline_population_fraction": 1}, "reason_codes": ["missing_economic_input"] if cat == "school" else [],
            "review_activity": {"value": 10, "source": "synthetic_category_baseline"},
            "proposal": {"geometry": footprint, "height_m": 7, "illustrative": True}})
    save(HERE / "comparison.json", {"parcel_id": parcel["parcel_id"], "mode": "fixture", "freshness": "fresh",
        "versions": {k: "checker-v1" for k in ("schema", "data", "model", "assumptions")}, "scoring_available": True,
        "geometry_hash": "checker-geometry-v1", "scoring_geometry_hash": "checker-geometry-v1",
        "active_dimensions": ranking["active_dimensions"], "effective_weights": ranking["effective_weights"], "alternatives": rows})
    records = []
    for i in range(4):
        item = dict(parcel, parcel_id=f"sf:checker-{i + 1:04d}", source_apns=[f"000100{i + 1}"])
        records.append(item)
    save(HERE / "provider_records.json", records)


if __name__ == "__main__":
    main()
