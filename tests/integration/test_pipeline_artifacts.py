"""Native field mapping tests use synthetic rows, never the real model test set."""

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

from .artifacts import read_json
from .model_audit import evaluate
from .oracle import CANDIDATES
from .pipeline_artifacts import audit_pipeline_artifacts, FEATURES
from .reporting import Report

FIXTURES = Path(__file__).parent / "fixtures/artifacts"


def write_table(directory, name, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pa.Table.from_pylist(rows)
    if name.endswith("geoparquet"):
        from pyproj import CRS
        crs = CRS.from_epsg(4326).to_json_dict()
        table = table.replace_schema_metadata({b"geo": json.dumps({"version": "1.1.0", "primary_column": "geometry", "columns": {
            key: {"encoding": "WKB", "geometry_types": [kind], "crs": crs} for key, kind in (("geometry", "Polygon"), ("representative_point", "Point"))}}).encode()})
    path = directory / name
    pq.write_table(table, path)
    manifest_path = directory / "source_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {"outputs": {}}
    manifest["outputs"][name] = {"rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps(manifest))


def native_features(row):
    names = {"nearest_peer_distance_m": "nearest_peer_m", "median_household_income_usd": "income_usd",
             "median_gross_rent_usd_month": "rent_usd", "population_density_missing": "density_missing",
             "median_household_income_missing": "income_missing", "median_gross_rent_missing": "rent_missing"}
    output = {}
    for feature in FEATURES:
        if feature == "category_code":
            value = CANDIDATES.index(row["category_id"])
        elif feature == "nearest_peer_missing":
            value = int(row["nearest_peer_m"] is None)
        else:
            value = row[names.get(feature, feature.replace("same_category_count_", "peer_count_"))]
        output[feature] = int(value) if isinstance(value, bool) else value
    return output


@pytest.fixture
def native_release(tmp_path):
    from shapely.geometry import shape
    directory = tmp_path / "native"
    directory.mkdir()
    places = read_json(FIXTURES / "places.json")
    for row in places:
        row["closure_status"], row["is_current_supply"] = "active", True
    by_place = {r["place_id"]: r for r in places}
    write_table(directory, "places.parquet", places)
    parcel = read_json(FIXTURES / "parcels.json")[0]
    parcel.update(geometry_quality="valid", site_label="hypothetical", vacancy_evidence="Synthetic unknown-vacancy site",
                  rep_lon=parcel["representative_point"]["coordinates"][0], rep_lat=parcel["representative_point"]["coordinates"][1])
    for key in ("geometry", "representative_point"):
        parcel[key] = shape(parcel[key]).wkb
    write_table(directory, "parcels.geoparquet", [parcel])
    demo = {"geoid": "060750101001", "geo_level": "block_group", "geometry": parcel["geometry"],
            "representative_point": parcel["representative_point"], "population": 300, "population_moe": 10,
            "land_area_m2": 10000, "population_density_per_km2": 30000, "median_household_income_usd": None,
            "median_household_income_moe_usd": None, "median_gross_rent_usd_month": 2000,
            "median_gross_rent_moe_usd_month": 50, "acs_vintage": "synthetic", "boundary_vintage": "synthetic"}
    write_table(directory, "demographics.geoparquet", [demo])
    write_table(directory, "tract_covariates.geoparquet", [{**demo, "geoid": "06075010100", "geo_level": "tract"}])
    existing_reviews = {r["place_id"]: r for r in read_json(FIXTURES / "review_summary.json")}
    reviews = [{"place_id": row["place_id"], "sampled_review_count": existing_reviews.get(row["place_id"], {}).get("sampled_review_count", 0),
                "valid_rating_count": 2 if row["place_id"] in existing_reviews else 0,
                "mean_rating": 4.0 if row["place_id"] in existing_reviews else None,
                "first_review_date": "2024-01-01" if row["place_id"] in existing_reviews else None,
                "last_review_date": "2025-01-01" if row["place_id"] in existing_reviews else None} for row in places]
    write_table(directory, "review_summary.parquet", reviews)
    features = [{"place_id": row["place_id"], "category": row["category_id"], "city": by_place[row["place_id"]]["city"],
                 "split": row["split"], "reviews_count": by_place[row["place_id"]]["reviews_count"], **native_features(row),
                 "target_log1p_reviews_count": row["target"], "labeled": True} for row in read_json(FIXTURES / "place_features.json")]
    write_table(directory, "place_features.parquet", features)
    write_table(directory, "splits.parquet", [{k: r[k] for k in ("place_id", "category", "city", "split", "labeled")} for r in features])
    write_table(directory, "parcel_features.parquet", [{"parcel_id": row["parcel_id"], "category": row["category_id"], **native_features(row)}
                                                      for row in read_json(FIXTURES / "parcel_features.json")])
    import statistics
    final_baselines = {c: statistics.median(r["target_log1p_reviews_count"] for r in features if r["category"] == c and r["split"] in {"train", "validation"}) for c in CANDIDATES}
    write_table(directory, "review_predictions.parquet", [{"parcel_id": parcel["parcel_id"], "category_id": c,
                "predicted_log1p_reviews_count": final_baselines[c], "source": "category_baseline"} for c in CANDIDATES])
    by_eval = {}
    for row in read_json(FIXTURES / "predictions.json"):
        id = row["place_id"]
        by_eval.setdefault(id, {"place_id": id, "category": row["category_id"], "city": row["city"],
            "split": "validation" if row["stage"] == "selection" else "test", "stage": "validation" if row["stage"] == "selection" else "test",
            "target_log1p_reviews_count": row["actual_log_count"], "pred_selected": row["prediction_log_count"],
            "pred_category_baseline": row["prediction_log_count"], "pred_category_city_baseline": row["prediction_log_count"]})
    write_table(directory, "place_eval_predictions.parquet", list(by_eval.values()))
    model = {"data_version": "checker-native-fixture", "model_categories": [], "degenerate_target_categories": [], "selected": "category_baseline"}
    for stage in ("validation", "test"):
        stats = evaluate([{"category_id": r["category"], "actual_log_count": r["target_log1p_reviews_count"],
                           "prediction_log_count": r["pred_selected"]} for r in by_eval.values() if r["stage"] == stage])
        advertised = {c: {"n": s["n"], "n_predicted": s["n"], "rmse_log_count": s["log_count_rmse"], "spearman": s["spearman"]}
                      for c, s in stats["categories"].items()}
        model[stage] = {key: advertised for key in ("selected", "category_baseline", "category_city_baseline")}
    (directory / "model_report.json").write_text(json.dumps(model))
    (directory / "feature_definitions.json").write_text(json.dumps({"columns": FEATURES, "crs": "EPSG:26910"}))
    manifest = read_json(directory / "source_manifest.json")
    manifest.update(data_version="checker-native-fixture", pipeline_version="checker-fixture", census_skipped=False,
                    sources={"synthetic": {"url": "https://github.com/Gilaze/urban_planner_helper", "sha256": "0" * 64, "retrieved_at": "2026-11-15T00:00:00+00:00"}})
    (directory / "source_manifest.json").write_text(json.dumps(manifest))
    return directory


def test_native_field_mapping_geometry_features_and_metrics(native_release):
    report = Report("fixture")
    metrics = audit_pipeline_artifacts(native_release, report)
    assert not any(c.status == "FAIL" for c in report.checks), [(c.id, c.detail) for c in report.checks if c.status == "FAIL"]
    assert metrics["test"]["pred_selected"]["categories"]["clinic"]["n"] == 1
    assert any(c.id == "pipeline.normalization_exports" and c.status == "BLOCKED" for c in report.checks)


@pytest.mark.parametrize("name,edit,expected_id", [
    ("parcels.geoparquet", lambda rows: rows[0].update(source_apns=[1001]), "pipeline.table.parcels.geoparquet"),
    ("demographics.geoparquet", lambda rows: rows[0].update(geo_level="tract"), "pipeline.table.demographics.geoparquet"),
    ("demographics.geoparquet", lambda rows: rows[0].update(population_density_per_km2=3), "pipeline.table.demographics.geoparquet"),
    ("places.parquet", lambda rows: rows[0].update(closure_status="closed_permanently"), "pipeline.table.places.parquet"),
    ("review_summary.parquet", lambda rows: rows.append(dict(rows[0])), "pipeline.table.review_summary.parquet"),
    ("place_features.parquet", lambda rows: rows[0].update(target_log1p_reviews_count=999), "pipeline.joins_splits_targets"),
    ("place_features.parquet", lambda rows: rows[0].update(same_category_count_400m=99), "pipeline.sampled_geometry_features"),
    ("review_predictions.parquet", lambda rows: rows[0].update(source="unavailable"), "pipeline.table.review_predictions.parquet"),
    ("review_predictions.parquet", lambda rows: rows[0].update(predicted_log1p_reviews_count=999), "pipeline.exported_model_metrics"),
    ("splits.parquet", lambda rows: rows[0].update(split="test"), "pipeline.joins_splits_targets"),
    ("place_eval_predictions.parquet", lambda rows: rows[0].update(pred_selected=None), "pipeline.exported_model_metrics"),
])
def test_native_corruption_is_detected_beyond_resigned_hashes(native_release, name, edit, expected_id):
    import pyarrow.parquet as pq
    rows = pq.read_table(native_release / name).to_pylist()
    edit(rows)
    write_table(native_release, name, rows)
    report = Report("fixture")
    audit_pipeline_artifacts(native_release, report)
    assert any(c.id == expected_id and c.status == "FAIL" for c in report.checks), [(c.id, c.status, c.detail) for c in report.checks]


def test_missing_native_release_is_blocked_not_passed(tmp_path):
    report = Report("release")
    assert audit_pipeline_artifacts(tmp_path, report) == {}
    assert report.failed and report.checks[0].status == "BLOCKED"


def test_report_reconciliation_never_claims_raw_prediction_validation():
    from .pipeline_artifacts import audit_pipeline_reports
    root = Path(__file__).resolve().parents[2]
    report = Report("fixture")
    summary = audit_pipeline_reports(root / "reports/data", report)
    assert summary["raw_prediction_metrics_verified"] is False
    assert any(c.id == "data_reports.gate_arithmetic" and c.status == "PASS" for c in report.checks), [(c.id, c.detail) for c in report.checks]


def test_report_reconciliation_detects_changed_gate(tmp_path):
    from .pipeline_artifacts import audit_pipeline_reports
    root = Path(__file__).resolve().parents[2]
    data = read_json(root / "reports/data/model_report.json")
    data["gate"]["best_model_macro_rmse"] += 1
    (tmp_path / "model_report.json").write_text(json.dumps(data))
    report = Report("fixture")
    audit_pipeline_reports(tmp_path, report)
    assert any(c.id == "data_reports.consistency" and c.status == "FAIL" and "gate arithmetic" in c.detail for c in report.checks)


@pytest.mark.parametrize("include_test_id", [False, True])
def test_native_normalization_evidence_can_close_or_fail_its_gate(native_release, include_test_id):
    proof = {stage: {"fit_place_ids": [], "categories": {}} for stage in ("selection", "final")}
    if include_test_id:
        proof["selection"]["fit_place_ids"] = ["clinic-t"]
    path = native_release / "model_normalization.json"
    path.write_text(json.dumps(proof))
    manifest = read_json(native_release / "source_manifest.json")
    manifest["outputs"][path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (native_release / "source_manifest.json").write_text(json.dumps(manifest))
    report = Report("fixture")
    audit_pipeline_artifacts(native_release, report)
    check = next(c for c in report.checks if c.id == "pipeline.normalization_exports")
    assert check.status == ("FAIL" if include_test_id else "PASS"), check.detail
