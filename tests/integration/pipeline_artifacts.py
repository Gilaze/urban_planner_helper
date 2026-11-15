"""Audit Person 2's published canonical schema without importing its transforms.

Large parcel/category tables stream in batches. Geometry and neighbor checks use
a reproducible sample (seed 42, up to 25 rows per table), explicitly reported as
such; keys, domains, target joins and fingerprints cover every exported row.
"""

import hashlib
import json
import math
import random
import re
import statistics
import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .artifacts import finite, read_json, _geometry, PRIVATE
from .model_audit import evaluate
from .oracle import CANDIDATES, CATEGORIES

FEATURES = ["category_code", *[f"same_category_count_{r}m" for r in (400, 800, 1600)],
            *[f"other_service_count_{r}m" for r in (400, 800, 1600)], "transit_count_400m", "transit_count_800m",
            "nearest_peer_distance_m", "population_density_per_km2", "median_household_income_usd",
            "median_gross_rent_usd_month", "nearest_peer_missing", "population_density_missing",
            "median_household_income_missing", "median_gross_rent_missing"]
TABLES = {
    "parcels.geoparquet": ({"parcel_id", "source_apns", "geometry", "representative_point", "rep_lon", "rep_lat", "area_m2",
                           "geometry_quality", "vacancy_status", "vacancy_evidence", "site_label", "source_vintage"}, ("parcel_id",)),
    "places.parquet": ({"place_id", "longitude", "latitude", "city", "primary_category", "service_tags", "closure_status", "is_current_supply", "reviews_count"}, ("place_id",)),
    "review_summary.parquet": ({"place_id", "sampled_review_count", "valid_rating_count", "mean_rating", "first_review_date", "last_review_date"}, ("place_id",)),
    "demographics.geoparquet": ({"geoid", "geo_level", "geometry", "representative_point", "population", "population_moe", "land_area_m2",
                                "population_density_per_km2", "median_household_income_usd", "median_household_income_moe_usd",
                                "median_gross_rent_usd_month", "median_gross_rent_moe_usd_month", "acs_vintage", "boundary_vintage"}, ("geoid",)),
    "tract_covariates.geoparquet": ({"geoid", "geo_level", "geometry", "representative_point", "population", "land_area_m2", "population_density_per_km2"}, ("geoid",)),
    "place_features.parquet": ({"place_id", "category", "city", "split", "reviews_count", "target_log1p_reviews_count", "labeled", *FEATURES}, ("place_id",)),
    "parcel_features.parquet": ({"parcel_id", "category", *FEATURES}, ("parcel_id", "category")),
    "review_predictions.parquet": ({"parcel_id", "category_id", "predicted_log1p_reviews_count", "source"}, ("parcel_id", "category_id")),
    "place_eval_predictions.parquet": ({"place_id", "category", "city", "split", "stage", "target_log1p_reviews_count", "pred_selected",
                                        "pred_category_baseline", "pred_category_city_baseline"}, ("place_id",)),
    "splits.parquet": ({"place_id", "category", "city", "split", "labeled"}, ("place_id",)),
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _validate_row(name, row):
    if name == "parcels.geoparquet":
        require(row["parcel_id"].startswith("sf:"), "Physical parcel ID lacks sf: namespace")
        apns = row["source_apns"]
        require(isinstance(apns, list) and apns and all(isinstance(a, str) and a for a in apns) and len(set(apns)) == len(apns), "APNs must be unique nonempty strings")
        require(row["geometry_quality"] in {"valid", "repaired", "unusable"}, "Unknown geometry quality")
        if row["geometry_quality"] != "unusable":
            require(finite(row["area_m2"], low=0) and row["area_m2"] > 0, "Valid parcel lacks positive m2 area")
            require(finite(row["rep_lon"], low=-180, high=180) and finite(row["rep_lat"], low=-90, high=90), "Invalid representative coordinate")
        require(row["vacancy_status"] in {"source_indicated_vacant", "unknown", "not_vacant"}, "Unknown vacancy status")
        require(bool(row["source_vintage"]) and bool(row["vacancy_evidence"]), "Source vintage/vacancy evidence missing")
        require(row["site_label"] == ("source_indicated_vacancy" if row["vacancy_status"] == "source_indicated_vacant" else "hypothetical"), "Vacancy/hypothetical label disagrees")
    elif name == "places.parquet":
        require(finite(row["longitude"], low=-180, high=180) and finite(row["latitude"], low=-90, high=90), "Invalid WGS84 coordinate")
        require(row["primary_category"] in CATEGORIES, "Unaccepted primary category")
        tags = row["service_tags"]
        require(isinstance(tags, list) and all(t in CATEGORIES for t in tags) and len(set(tags)) == len(tags), "Invalid or duplicated service tags")
        status = row["closure_status"]
        require(status in {"active", "unknown", "closed_temporarily", "closed_permanently"}, "Unknown closure status")
        require(row["is_current_supply"] is (status in {"active", "unknown"}), "Closed supply included or unknown status silently excluded")
        count = row["reviews_count"]
        require(count is None or finite(count, low=0) and int(count) == count, "Invalid review count")
    elif name == "review_summary.parquet":
        require(all(finite(row[k], low=0) and int(row[k]) == row[k] for k in ("sampled_review_count", "valid_rating_count")), "Invalid review counts")
        require(row["valid_rating_count"] <= row["sampled_review_count"] and finite(row["mean_rating"], low=1, high=5, nullable=True), "Invalid review rating/count")
        require((row["mean_rating"] is None) == (row["valid_rating_count"] == 0), "Mean rating must be null exactly when no valid ratings exist")
        start, end = row["first_review_date"], row["last_review_date"]
        require((start is None) == (end is None) and (start is None or start <= end), "Invalid review date coverage")
    elif name in {"demographics.geoparquet", "tract_covariates.geoparquet"}:
        level = "block_group" if name == "demographics.geoparquet" else "tract"
        require(row["geo_level"] == level and re.fullmatch(r"\d{12}" if level == "block_group" else r"\d{11}", row["geoid"]), "Mixed population geography or nonstring GEOID")
        require(finite(row["population"], low=0, nullable=True) and finite(row["land_area_m2"], low=0), "Invalid Census population/land area")
        density = row["population_density_per_km2"]
        expected = row["population"] / row["land_area_m2"] * 1e6 if row["population"] is not None and row["land_area_m2"] > 0 else None
        require(density is None if expected is None else finite(density) and math.isclose(density, expected, rel_tol=1e-8, abs_tol=1e-8), "Density units or denominator differ")
        for field in ("population_moe", "median_household_income_usd", "median_household_income_moe_usd",
                      "median_gross_rent_usd_month", "median_gross_rent_moe_usd_month"):
            if field in row:
                require(finite(row[field], low=0, nullable=True), f"Invalid estimate/MOE {field}")
    elif name.endswith("features.parquet"):
        require(row["category"] in CANDIDATES and row["category_code"] == CANDIDATES.index(row["category"]), "Feature category/code differs")
        for field, missing in (("nearest_peer_distance_m", "nearest_peer_missing"), ("population_density_per_km2", "population_density_missing"),
                               ("median_household_income_usd", "median_household_income_missing"), ("median_gross_rent_usd_month", "median_gross_rent_missing")):
            require(finite(row[field], low=0, nullable=True) and row[missing] in (0, 1) and bool(row[missing]) == (row[field] is None), f"Invalid {field}/missing flag")
        require(all(finite(row[c], low=0) and int(row[c]) == row[c] for c in FEATURES if "_count_" in c), "Invalid count predictor")
        if name == "parcel_features.parquet":
            require(not {"target_log1p_reviews_count", "split", "labeled"} & row.keys(), "Training metadata leaked into parcel features")
    elif name == "review_predictions.parquet":
        require(row["category_id"] in CANDIDATES and row["source"] in {"model", "category_baseline", "unavailable"}, "Unknown prediction category/source")
        require(finite(row["predicted_log1p_reviews_count"], low=0, nullable=True), "Invalid review-activity prediction")
        require((row["predicted_log1p_reviews_count"] is None) == (row["source"] == "unavailable"), "Prediction null/source disagrees")


def audit_pipeline_artifacts(directory, report):
    directory = Path(directory)
    command = f"python -m tests.integration.verify_release --artifact-format pipeline --artifact-dir '{directory}'"
    tables, samples, masks, geometry_crs = {}, {}, {}, {}
    try:
        manifest = read_json(directory / "source_manifest.json")
        require(isinstance(manifest["outputs"], dict) and manifest.get("data_version") and manifest.get("pipeline_version"), "Missing pipeline versions/output fingerprints")
        require(type(manifest.get("census_skipped")) is bool, "Census coverage state must be explicit")
        require(isinstance(manifest["sources"], dict) and bool(manifest["sources"]), "Source inventory missing")
        for source in manifest["sources"].values():
            require(source.get("url") and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]), "Source reference/hash missing")
            require(datetime.fromisoformat(source["retrieved_at"].replace("Z", "+00:00")).tzinfo is not None, "Source retrieval timestamp needs timezone")
        report.add("pipeline.manifest", "PASS", "Data", f"Canonical release {manifest['data_version']}; source fingerprints/versions present.", command, "Versioned native source manifest.", impact="provenance")
        report.add("pipeline.census_coverage", "FAIL" if manifest["census_skipped"] else "PASS", "Data",
                   f"census_skipped={manifest['census_skipped']}; skipped Census cannot support live access metrics or covariate slices.", command, "Census populated before live release.")
    except Exception as exc:
        report.add("pipeline.manifest", "BLOCKED" if isinstance(exc, FileNotFoundError) else "FAIL", "Data", exc, command, "Full canonical pipeline release, not just serving projection.")
        return {}
    for name, (required, keys) in TABLES.items():
        try:
            import pyarrow.parquet as pq
            path = directory / name
            parquet = pq.ParquetFile(path)
            columns = parquet.schema_arrow.names
            require(required <= set(columns), f"Missing columns {sorted(required - set(columns))}")
            require(not PRIVATE & set(columns), "Reviewer identity/raw review text in output")
            meta = manifest["outputs"][name]
            require(meta["rows"] == parquet.metadata.num_rows and meta["sha256"] == sha256(path), "Native manifest fingerprint/row count mismatch")
            if name.endswith("features.parquet"):
                require([c for c in columns if c in FEATURES] == FEATURES, "Shared feature order differs")
            geo = json.loads((parquet.schema_arrow.metadata or {}).get(b"geo", b"{}"))
            if name.endswith("geoparquet"):
                require(geo.get("primary_column") == "geometry", "Missing GeoParquet metadata")
                geometry_crs[name] = geo["columns"]["geometry"].get("crs", "EPSG:4326")
            rng, sample, count, seen = random.Random(42), [], 0, set()
            streamed = name in {"parcel_features.parquet", "review_predictions.parquet"}
            rows, category_masks = [], {}
            for batch in parquet.iter_batches(batch_size=4096):
                for row in batch.to_pylist():
                    count += 1
                    key = tuple(row[k] for k in keys)
                    require(all(isinstance(k, str) and k for k in key), "Keys must be nonempty strings")
                    if streamed:
                        category = row[keys[1]]
                        require(category in CANDIDATES, "Unknown candidate ID")
                        bit = 1 << CANDIDATES.index(category)
                        require(not category_masks.get(key[0], 0) & bit, "Duplicate parcel/category key")
                        category_masks[key[0]] = category_masks.get(key[0], 0) | bit
                    else:
                        require(key not in seen, f"Duplicate key {key}")
                        seen.add(key)
                        rows.append(row)
                    _validate_row(name, row)
                    if len(sample) < 25:
                        sample.append(row)
                    else:
                        replace = rng.randrange(count)
                        if replace < 25:
                            sample[replace] = row
            require(count > 0, "Empty canonical table")
            tables[name], samples[name] = rows, sample
            if streamed:
                masks[name] = category_masks
            report.add(f"pipeline.table.{name}", "PASS", "Data", f"{count} rows; full key/domain/fingerprint checks; {len(sample)} deterministic sampled rows retained.", command, "Native columns, unique keys, valid domains and exact file fingerprints.", impact="data integrity")
        except Exception as exc:
            report.add(f"pipeline.table.{name}", "BLOCKED" if isinstance(exc, FileNotFoundError) else "FAIL", "Data", exc, command, "Valid canonical table with its matching fingerprint.")
    try:
        parcels = {r["parcel_id"]: r for r in tables["parcels.geoparquet"]}
        places = {r["place_id"]: r for r in tables["places.parquet"]}
        features = {r["place_id"]: r for r in tables["place_features.parquet"]}
        apns = [a for row in parcels.values() for a in row["source_apns"]]
        require(len(set(apns)) == len(apns), "APN assigned to more than one physical lot")
        require({r["place_id"] for r in tables["review_summary.parquet"]} == set(places), "Places/review-summary left join changed row universe")
        split_by_city = {"Oakland": "train", "Berkeley": "validation", "San Francisco": "test"}
        expected_training = {key for key, row in places.items() if row["city"] in split_by_city and row["primary_category"] in CANDIDATES and row["closure_status"] != "closed_permanently"}
        require(set(features) == expected_training, "Training eligibility changed the expected Places row universe")
        for key, row in features.items():
            source = places[key]
            require(row["split"] == split_by_city[row["city"]] and row["city"] == source["city"] and row["category"] == source["primary_category"], "Geographic split or training join differs")
            count = source["reviews_count"]
            require(row["reviews_count"] == count and row["labeled"] is (count is not None), "Training labels differ from Places")
            target = row["target_log1p_reviews_count"]
            require(target is None if count is None else finite(target) and math.isclose(target, math.log1p(count), abs_tol=1e-10), "Target not log1p Places reviews_count")
        require({r["place_id"] for r in tables["splits.parquet"]} == set(features), "Split export changes training universe")
        require(all(all(row[field] == features[row["place_id"]][field] for field in ("category", "city", "split", "labeled")) for row in tables["splits.parquet"]), "Split export metadata differs from feature rows")
        expected = {key: 31 for key, row in parcels.items() if row["rep_lon"] is not None and row["rep_lat"] is not None}
        require(masks["parcel_features.parquet"] == expected == masks["review_predictions.parquet"], "Parcel feature/prediction universe or five-candidate coverage differs")
        report.add("pipeline.joins_splits_targets", "PASS", "Data", "APN physical-lot mapping, left join, geographic splits, log targets and scenario universes agree.", command, "No row multiplication, target leakage or orphan scenario.", impact="join/model integrity")
    except Exception as exc:
        report.add("pipeline.joins_splits_targets", "FAIL", "Data", exc, command, "Consistent canonical grains, joins and geographic targets.")
        return {}
    try:
        definitions = read_json(directory / "feature_definitions.json")
        require(definitions["columns"] == FEATURES and definitions["crs"] == "EPSG:26910", "Feature definitions/order/metric CRS differ")
        from pyproj import Transformer
        project = Transformer.from_crs(4326, 26910, always_xy=True).transform
        supply = [(key, project(r["longitude"], r["latitude"]), set(r["service_tags"])) for key, r in places.items() if r["is_current_supply"]]
        for name, sample in samples.items():
            if name.endswith("geoparquet"):
                for row in sample:
                    if name == "parcels.geoparquet" and row["geometry_quality"] == "unusable":
                        continue
                    polygon, metric = _geometry(row["geometry"], geometry_crs[name], polygon=True)
                    point, _ = _geometry(row["representative_point"], geometry_crs[name], polygon=False)
                    require(polygon.covers(point), "Representative point outside polygon")
                    if name == "parcels.geoparquet":
                        require(abs(metric.area - row["area_m2"]) <= max(1, metric.area * .02), "Area not in metric CRS units")
        for name in ("place_features.parquet", "parcel_features.parquet"):
            for row in samples[name]:
                focal = row.get("place_id")
                origin = project(places[focal]["longitude"], places[focal]["latitude"]) if focal else project(parcels[row["parcel_id"]]["rep_lon"], parcels[row["parcel_id"]]["rep_lat"])
                distances = [(math.dist(origin, point), tags) for key, point, tags in supply if key != focal]
                for radius in (400, 800, 1600):
                    expected_peer = sum(d <= radius and row["category"] in tags for d, tags in distances)
                    expected_other = sum(d <= radius and row["category"] not in tags and "transit" not in tags for d, tags in distances)
                    require(row[f"same_category_count_{radius}m"] == expected_peer and row[f"other_service_count_{radius}m"] == expected_other, "Sampled neighbor count/focal/closure/tag mismatch")
                    if radius <= 800:
                        require(row[f"transit_count_{radius}m"] == sum(d <= radius and "transit" in tags for d, tags in distances), "Sampled transit count differs")
                nearest = min((d for d, tags in distances if row["category"] in tags), default=None)
                require(row["nearest_peer_distance_m"] is None if nearest is None else finite(row["nearest_peer_distance_m"]) and math.isclose(row["nearest_peer_distance_m"], nearest, abs_tol=.01), "Sampled nearest-peer distance differs")
        report.add("pipeline.sampled_geometry_features", "PASS", "Data", "Up to 25 seeded rows per geometry/feature table independently checked; predictor columns exclude target-derived fields.", command, "Sampled geometry/area, EPSG:26910 counts, focal exclusion and training/parcel parity.", impact="sampled spatial integrity")
    except Exception as exc:
        report.add("pipeline.sampled_geometry_features", "FAIL", "Data", exc, command, "Matching feature definitions and independent sampled spatial checks.")
    return audit_native_model(directory, tables, manifest, report, command)


def audit_native_model(directory, tables, manifest, report, command):
    output = {}
    try:
        model = read_json(directory / "model_report.json")
        require(model["data_version"] == manifest["data_version"], "Model report/release version differs")
        features = {r["place_id"]: r for r in tables["place_features.parquet"]}
        predictions = tables["place_eval_predictions.parquet"]
        degenerate = set(model.get("degenerate_target_categories", []))
        final_baselines = defaultdict(list)
        for row in features.values():
            if row["split"] in {"train", "validation"} and row["labeled"] and row["category"] not in degenerate:
                final_baselines[row["category"]].append(row["target_log1p_reviews_count"])
        final_baselines = {c: statistics.median(v) for c, v in final_baselines.items()}
        import pyarrow.parquet as pq
        for batch in pq.ParquetFile(directory / "review_predictions.parquet").iter_batches(batch_size=4096, columns=["category_id", "source", "predicted_log1p_reviews_count"]):
            for row in batch.to_pylist():
                if row["source"] == "category_baseline":
                    expected = final_baselines.get(row["category_id"])
                    require(expected is not None and math.isclose(row["predicted_log1p_reviews_count"], expected, abs_tol=1e-10), "Serving category baseline differs from Oakland+Berkeley training rows")
                if row["source"] == "model":
                    require(model["selected"] == "model" and row["category_id"] in model["model_categories"], "Unsupported category served by learned model")
        for stage, train_splits, split in (("validation", {"train"}, "validation"), ("test", {"train", "validation"}, "test")):
            fit = [r for r in features.values() if r["split"] in train_splits and r["labeled"] and r["category"] not in degenerate]
            by_category = defaultdict(list)
            for row in fit:
                by_category[row["category"]].append(row["target_log1p_reviews_count"])
            baselines = {c: statistics.median(v) for c, v in by_category.items()}
            subset = [r for r in predictions if r["stage"] == stage]
            expected_ids = {key for key, r in features.items() if r["split"] == split and r["labeled"] and r["category"] not in degenerate}
            require({r["place_id"] for r in subset} == expected_ids, "Evaluation row universe differs from labeled split")
            for row in subset:
                raw = features[row["place_id"]]
                require(row["city"] == raw["city"] and row["split"] == split and row["category"] == raw["category"] and row["target_log1p_reviews_count"] == raw["target_log1p_reviews_count"], "Evaluation join/target differs")
                for field in ("pred_category_baseline", "pred_category_city_baseline"):
                    require(finite(row[field]) and math.isclose(row[field], baselines[row["category"]], abs_tol=1e-10), "Unseen evaluation city baseline not fitted on permitted training rows")
                require(finite(row["pred_selected"]), "Selected evaluation omits a prediction served through category-baseline fallback")
            output[stage] = {field: evaluate([{"category_id": r["category"], "actual_log_count": r["target_log1p_reviews_count"],
                                              "prediction_log_count": r[field]} for r in subset])
                             for field in ("pred_selected", "pred_category_baseline", "pred_category_city_baseline")}
            for field, key in (("pred_selected", "selected"), ("pred_category_baseline", "category_baseline"), ("pred_category_city_baseline", "category_city_baseline")):
                if key not in model[stage]:
                    continue
                for category, advertised in model[stage][key].items():
                    calculated = output[stage][field]["categories"].get(category)
                    if not advertised["n_predicted"]:
                        continue
                    require(calculated is not None and math.isclose(advertised["rmse_log_count"], calculated["log_count_rmse"], abs_tol=1e-8), "Exported RMSE differs from independent recalculation")
                    expected_sp = calculated["spearman"]
                    require(advertised["spearman"] is None if expected_sp is None else math.isclose(advertised["spearman"], expected_sp, abs_tol=1e-8), "Exported Spearman differs")
        report.add("pipeline.exported_model_metrics", "PASS", "Data", "Recomputed Berkeley and SF metrics, baseline fallback and common rows independently; see model_metrics.json.", command, "Frozen prediction metrics match the serving behavior and geographic protocol.", impact="model integrity")
        try:
            audit_normalization_export(directory, features, model, manifest)
            report.add("pipeline.normalization_exports", "PASS", "Data", "Selection/final fit IDs and category mean/std match permitted training rows.", command,
                       "Versioned, fingerprinted normalization evidence without loading estimator pickles.", impact="model integrity")
        except FileNotFoundError:
            report.add("pipeline.normalization_exports", "BLOCKED", "Data", "No model_normalization.json export; estimator pickles were not loaded.", command,
                       "Export selection/final fit_place_ids and categories {mean, std}, including empty stages when no model was fitted.")
        except Exception as exc:
            report.add("pipeline.normalization_exports", "FAIL", "Data", exc, command, "Normalization fitted only on the permitted cities/categories.")
    except Exception as exc:
        report.add("pipeline.exported_model_metrics", "BLOCKED" if isinstance(exc, FileNotFoundError) else "FAIL", "Data", exc, command, "Full frozen evaluation and transform evidence.")
    return output


def audit_normalization_export(directory, features, model, manifest):
    path = Path(directory) / "model_normalization.json"
    actual = read_json(path)
    require(manifest["outputs"][path.name]["sha256"] == sha256(path), "Normalization fingerprint differs")
    categories = set(model["model_categories"])
    for stage, splits in (("selection", {"train"}), ("final", {"train", "validation"})):
        use_categories = categories if stage == "selection" or model["selected"] == "model" else set()
        fit = {key: r for key, r in features.items() if r["category"] in use_categories and r["split"] in splits and r["labeled"]}
        ids = actual[stage]["fit_place_ids"]
        require(len(ids) == len(set(ids)) and set(ids) == set(fit), f"{stage} normalization fit IDs include wrong cities/categories")
        grouped = defaultdict(list)
        for row in fit.values():
            grouped[row["category"]].append(row["target_log1p_reviews_count"])
        require(set(actual[stage]["categories"]) == set(grouped), "Normalization category coverage differs")
        for category, values in grouped.items():
            expected = {"mean": statistics.mean(values), "std": statistics.pstdev(values) or 1.0}
            require(all(finite(actual[stage]["categories"][category][k]) and math.isclose(actual[stage]["categories"][category][k], v, abs_tol=1e-10)
                        for k, v in expected.items()), f"{stage}/{category} normalization includes nontraining labels")


def audit_pipeline_reports(directory, report):
    """Reconcile committed aggregate reports; never claim raw metric verification."""
    directory = Path(directory)
    command = f"python -m tests.integration.verify_release --data-reports-dir '{directory}'"
    result = {"evidence_kind": "reported-aggregate-reconciliation", "raw_prediction_metrics_verified": False}
    try:
        model = read_json(directory / "model_report.json")
        counts = model["labeled_counts"]
        supported = {c for c, values in counts.items() if values["train"] >= 100 and values["validation"] >= 30}
        require(supported == set(model["model_categories"]), "Reported support categories violate 100/30 rule")
        baseline = model["validation"]["category_baseline"]
        reference = statistics.mean(baseline[c]["rmse_log_count"] for c in supported) if supported else None
        candidates = model["validation"]["candidates"]
        for candidate in candidates:
            expected = statistics.mean(candidate["per_category"][c]["rmse_log_count"] for c in supported)
            require(math.isclose(expected, candidate["macro_rmse"], abs_tol=1e-10), "Reported candidate macro RMSE differs from category mean")
        best = min((r["macro_rmse"] for r in candidates), default=None)
        passed = bool(best is not None and reference and best <= .95 * reference)
        gate = model["gate"]
        baseline_matches = gate["category_baseline_macro_rmse"] is None if reference is None else math.isclose(reference, gate["category_baseline_macro_rmse"], abs_tol=1e-10)
        require(baseline_matches and best == gate["best_model_macro_rmse"] and passed == gate["passed"], "Reported Berkeley gate arithmetic differs")
        require(model["selected"] == ("model" if passed else "category_baseline"), "Selected approach differs from reported gate")
        result.update(reported_data_version=model["data_version"], reported_gate_improvement=gate["relative_improvement"],
                      reported_census_skipped=model["census_skipped"])
        report.add("data_reports.gate_arithmetic", "PASS", "Data", "Reported per-category RMSE, macro averages, support minimums and Berkeley gate reconcile. This is aggregate arithmetic, not verification of raw predictions.", command,
                   "Reported gate is internally consistent.", impact="reported aggregate consistency")
        report.add("data_reports.census", "BLOCKED" if model["census_skipped"] else "PASS", "Data",
                   f"Data report declares census_skipped={model['census_skipped']}; verify canonical population/coverage before live use.", command,
                   "Census-backed population and covariates exported with margins of error.")
        selected, baseline = model["test"]["selected"], model["test"]["category_baseline"]
        common = [c for c in selected if selected[c]["n_predicted"] and baseline[c]["n_predicted"]]
        result["reported_common_sf_categories"] = common
        if common:
            chosen = statistics.mean(selected[c]["rmse_log_count"] for c in common)
            reference = statistics.mean(baseline[c]["rmse_log_count"] for c in common)
            result.update(reported_sf_selected_common_macro_rmse=chosen, reported_sf_baseline_common_macro_rmse=reference)
            report.add("data_reports.sf_generalization", "WARN" if chosen > reference else "PASS", "Data",
                       f"On reported common SF categories {common}, selected macro RMSE={chosen:.6f}, baseline={reference:.6f}. Raw prediction files are unavailable in this checkout; these are reported aggregates, not a fresh SF evaluation.", command,
                       "Describe held-out performance and keep SF out of subsequent model selection.", impact="model interpretation")
        with (directory / "category_audit_sample.csv").open(newline="") as file:
            rows = list(csv.DictReader(file))
        by_reason = dict(Counter(r["audit_reason"] for r in rows))
        required = {f"candidate:{c}" for c in CANDIDATES} | {"ambiguous", "lookalike:clinic", "lookalike:grocery"}
        require(all(by_reason.get(reason, 0) >= 20 for reason in required), "Category audit sample lacks required candidate/ambiguous/lookalike rows")
        accepted = sum(r["reviewer_verdict"].strip().lower() == "accept" for r in rows)
        rejected = sum(r["reviewer_verdict"].strip().lower() == "reject" for r in rows)
        result.update(category_audit_rows=len(rows), category_audit_by_reason=by_reason, accepted_verdicts=accepted, rejected_verdicts=rejected)
        report.add("data_reports.category_review", "FAIL" if rejected else "PASS" if accepted == len(rows) else "BLOCKED", "Data + Checker",
                   f"{len(rows)} sampled rows; {accepted} accepted, {rejected} rejected; blank/uncertain verdicts remain unreviewed. Samples alone do not establish classification accuracy.", command,
                   "Review mappings against source evidence, resolve rejects, and record accept/reject verdicts.")
        result["source_sha256"] = {name: sha256(directory / name) for name in ("model_report.json", "category_audit_sample.csv")}
    except Exception as exc:
        report.add("data_reports.consistency", "BLOCKED" if isinstance(exc, FileNotFoundError) else "FAIL", "Data", exc, command,
                   "Complete native Data reports with internally consistent metrics and audit samples.")
    return result
