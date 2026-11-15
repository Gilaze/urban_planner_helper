import copy
import hashlib
import json
import math
import statistics
import shutil
from pathlib import Path

import pytest

from .artifacts import _read_table, validate_artifacts
from .model_audit import audit_model, category_normalization, rmse, spearman
from .reporting import Report

FIXTURES = Path(__file__).parent / "fixtures" / "artifacts"


@pytest.fixture
def artifacts(tmp_path):
    target = tmp_path / "artifacts"
    shutil.copytree(FIXTURES, target)
    return target


def mutate(directory, table, edit):
    path = directory / f"{table}.json"
    rows = json.loads(path.read_text())
    edit(rows)
    path.write_text(json.dumps(rows, indent=2) + "\n")
    manifest_path = directory / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if table in manifest["artifacts"]:
        manifest["artifacts"][table].update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), row_count=len(rows))
    manifest_path.write_text(json.dumps(manifest))


def checks(directory):
    report = Report("fixture")
    tables = validate_artifacts(directory, report)
    return report, tables


def test_complete_synthetic_artifacts_and_model_are_auditable(artifacts):
    report, tables = checks(artifacts)
    metrics = audit_model(artifacts, tables, report)
    assert not report.failed, [(c.id, c.detail) for c in report.checks if c.status != "PASS"]
    assert metrics["selection"]["evaluation_city"] == "Berkeley"
    assert metrics["final"]["evaluation_city"] == "San Francisco"
    assert all(s["status"] == "insufficient" and s["metrics"] is None for s in metrics["final"]["slices"].values())
    assert metrics["final"]["worst_errors"]


@pytest.mark.parametrize("table,edit,check_id", [
    ("parcels", lambda rows: rows[0].update(source_apns=[1001]), "artifacts.parcels"),
    ("parcels", lambda rows: rows[0].update(source_vintage=None), "artifacts.parcels"),
    ("parcels", lambda rows: rows[0].update(area_m2=999999), "artifacts.parcels"),
    ("parcels", lambda rows: rows[0].update(hypothetical=False), "artifacts.parcels"),
    ("parcels", lambda rows: rows[0].update(vacancy_status="source_indicated_vacant"), "artifacts.parcels"),
    ("parcels", lambda rows: rows.append(dict(rows[0], parcel_id="sf:other")), "joins.physical_parcels"),
    ("places", lambda rows: rows[0].update(latitude=-122.4, longitude=37.7), "artifacts.places"),
    ("places", lambda rows: rows[0].update(primary_category="supermarket"), "artifacts.places"),
    ("places", lambda rows: rows[0].update(service_tags=["clinic", "clinic"]), "artifacts.places"),
    ("places", lambda rows: rows[0].update(reviews_count=-1), "artifacts.places"),
    ("review_summary", lambda rows: rows.append(copy.deepcopy(rows[0])), "artifacts.review_summary"),
    ("review_summary", lambda rows: rows[0].update(reviewer_name="PRIVATE"), "artifacts.review_summary"),
    ("review_summary", lambda rows: rows[0].update(valid_date_end="2020-01-01"), "artifacts.review_summary"),
    ("demographics", lambda rows: rows[0].update(geoid=60750101001), "artifacts.demographics"),
    ("demographics", lambda rows: rows[0].update(population=-10), "artifacts.demographics"),
    ("parcel_features", lambda rows: rows[0].update(target=123), "artifacts.parcel_features"),
    ("parcel_features", lambda rows: rows.pop(), "joins.parcel_scenarios"),
    ("place_features", lambda rows: rows[0].update(place_id="unmatched"), "joins.training_places"),
])
def test_semantic_corruption_is_caught_even_with_updated_checksums(artifacts, table, edit, check_id):
    mutate(artifacts, table, edit)
    report, _ = checks(artifacts)
    assert any(c.id == check_id and c.status == "FAIL" for c in report.checks), [(c.id, c.detail) for c in report.checks]


def test_manifest_detects_unannounced_changes(artifacts):
    path = artifacts / "places.json"
    path.write_text(path.read_text() + " ")
    report, _ = checks(artifacts)
    assert any(c.id == "artifacts.places" and c.status == "FAIL" and "SHA256" in c.detail for c in report.checks)


def test_fixture_cannot_pass_live_release(artifacts, tmp_path):
    report = Report("release")
    validate_artifacts(artifacts, report)
    payload = report.write(tmp_path / "report")
    assert report.failed and payload["release_accepted"] is False


def test_geoparquet_decoding_and_declared_crs(artifacts):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shapely.geometry import shape
    from pyproj import CRS
    path = artifacts / "parcels.json"
    rows = json.loads(path.read_text())
    for row in rows:
        for col in ("geometry", "representative_point"):
            row[col] = shape(row[col]).wkb
    table = pa.Table.from_pylist(rows)
    geo = {"version": "1.1.0", "primary_column": "geometry", "columns": {
        key: {"encoding": "WKB", "geometry_types": [kind], "crs": CRS.from_epsg(4326).to_json_dict()}
        for key, kind in (("geometry", "Polygon"), ("representative_point", "Point"))}}
    table = table.replace_schema_metadata({b"geo": json.dumps(geo).encode()})
    output = artifacts / "parcels.geoparquet"
    pq.write_table(table, output)
    manifest_path = artifacts / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["parcels"].update(file=output.name, sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest))
    decoded, metadata = _read_table(output)
    assert isinstance(decoded[0]["geometry"], bytes) and metadata["version"] == "1.1.0"
    report, _ = checks(artifacts)
    assert not report.failed, [(c.id, c.detail) for c in report.checks if c.status == "FAIL"]


@pytest.mark.parametrize("edit,expected", [
    (lambda rows: rows[0].update(split="test"), "split"),
    (lambda rows: rows[0].update(target=2), "Target"),
    (lambda rows: rows[0].update(peer_count_400m=99), "Spatial counts"),
    (lambda rows: rows[0].update(income_missing=True), "Missingness"),
])
def test_model_split_target_focal_and_missingness_corruption(artifacts, edit, expected):
    mutate(artifacts, "place_features", edit)
    report, tables = checks(artifacts)
    audit_model(artifacts, tables, report)
    assert any(c.status == "FAIL" and expected in c.detail for c in report.checks)


def test_review_derived_predictor_leakage(artifacts):
    path = artifacts / "feature_definitions.json"
    data = json.loads(path.read_text())
    data["feature_names"].append("review_count")
    path.write_text(json.dumps(data))
    report, tables = checks(artifacts)
    audit_model(artifacts, tables, report)
    assert any(c.id == "model.features" and c.status == "FAIL" and "derived" in c.detail for c in report.checks)


@pytest.mark.parametrize("edit,expected", [
    (lambda m: m["normalization"]["selection"]["clinic"].update(mean=0), "normalization"),
    (lambda m: m["fit_place_ids"]["selection"].append("clinic-t"), "fit IDs"),
    (lambda m: m["slice_thresholds"]["selection"].update(income_usd=99999), "threshold"),
    (lambda m: m.update(supported_model_categories=["clinic"]), "100 Oakland"),
    (lambda m: m.update(sf_evaluation_count=2), "single final SF"),
])
def test_model_transform_and_evaluation_evidence(artifacts, edit, expected):
    path = artifacts / "model_manifest.json"
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data))
    report, tables = checks(artifacts)
    audit_model(artifacts, tables, report)
    assert any(c.id == "model.evaluation" and c.status == "FAIL" and expected in c.detail for c in report.checks)


def test_evaluation_requires_common_rows(artifacts):
    mutate(artifacts, "predictions", lambda rows: rows.pop())
    report, tables = checks(artifacts)
    audit_model(artifacts, tables, report)
    assert any(c.id == "model.evaluation" and c.status == "FAIL" and "identical evaluation" in c.detail for c in report.checks)


def test_metrics_ties_constant_ranks_and_train_only_zero_std():
    assert rmse([0, 2], [0, 0]) == pytest.approx(2 ** .5)
    assert spearman([1, 2, 2, 3], [4, 3, 3, 1]) == pytest.approx(-1)
    assert spearman([1, 1, 1], [2, 3, 4]) is None
    assert category_normalization([{"primary_category": "clinic", "reviews_count": 0}]) == {"clinic": {"mean": 0, "std": 1}}


@pytest.mark.parametrize("error_fraction,selected,expected_status", [(.94, "model", "PASS"), (.96, "category_baseline", "PASS"), (.96, "model", "FAIL")])
def test_model_selection_uses_berkeley_improvement_gate(tmp_path, monkeypatch, error_fraction, selected, expected_status):
    # Isolate evaluation logic from the separately tested geometry/predictor audit.
    monkeypatch.setattr("tests.integration.model_audit.audit_features", lambda *args: None)
    places, features = [], []
    for city, count, split in (("Oakland", 100, "train"), ("Berkeley", 30, "validation"), ("San Francisco", 1, "test")):
        for i in range(count):
            key = f"{city}-{i}"
            reviews = 0 if city == "Oakland" and i < 50 else 3
            places.append({"place_id": key, "city": city, "primary_category": "clinic", "reviews_count": reviews})
            features.append({"place_id": key, "category_id": "clinic", "target": math.log1p(reviews), "split": split,
                             "income_usd": 50000, "population_density_per_km2": 1000})
    manifest = {"model_attempted": True, "supported_model_categories": ["clinic"], "selected_approach": selected,
                "fit_place_ids": {}, "normalization": {}, "slice_thresholds": {}, "seed": 42,
                "estimator_version": "test-declared", "sf_evaluation_count": 1,
                "estimator": {"name": "HistGradientBoostingRegressor", "parameters": {"learning_rate": .05, "max_iter": 200,
                    "min_samples_leaf": 20, "early_stopping": False, "random_state": 42, "max_leaf_nodes": 15, "l2_regularization": 0}},
                "selection_trials": [{"max_leaf_nodes": leaves, "l2_regularization": l2} for leaves in (15, 31) for l2 in (0, 1)]}
    predictions = []
    for stage, cities, evaluation_city in (("selection", {"Oakland"}, "Berkeley"), ("final", {"Oakland", "Berkeley"}, "San Francisco")):
        fit = [r for r in places if r["city"] in cities]
        manifest["fit_place_ids"][stage] = [r["place_id"] for r in fit]
        manifest["normalization"][stage] = category_normalization(fit)
        manifest["slice_thresholds"][stage] = {"income_usd": 50000, "population_density_per_km2": 1000}
        baseline = statistics.median(math.log1p(r["reviews_count"]) for r in fit)
        for row in (r for r in places if r["city"] == evaluation_city):
            for approach in ("model", "category_baseline", "category_city_baseline"):
                model_prediction = math.log(4) - error_fraction * math.log(2) if stage == "selection" else math.log(4)
                predictions.append({"stage": stage, "approach": approach, "place_id": row["place_id"], "category_id": "clinic",
                    "city": row["city"], "actual_log_count": math.log1p(row["reviews_count"]),
                    "prediction_log_count": model_prediction if approach == "model" else baseline})
    (tmp_path / "model_manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "predictions.json").write_text(json.dumps(predictions))
    report = Report("fixture")
    audit_model(tmp_path, {"places": places, "place_features": features}, report)
    check = next(c for c in report.checks if c.id == "model.evaluation")
    assert check.status == expected_status, check.detail
    if expected_status == "FAIL":
        assert "5% Berkeley" in check.detail
