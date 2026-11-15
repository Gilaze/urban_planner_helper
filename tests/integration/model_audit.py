"""Recalculate exported metrics and inspect geographic/leakage evidence."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .artifacts import finite, read_json, _read_table, _geometry
from .oracle import CANDIDATES, CATEGORIES
from .reporting import Report

SPATIAL = [f"{kind}_count_{radius}m" for kind in ("peer", "other_service") for radius in (400, 800, 1600)] + [
    "transit_count_400m", "transit_count_800m", "nearest_peer_m",
]
PREDICTORS = SPATIAL + ["population_density_per_km2", "income_usd", "rent_usd", "category_id",
                         "density_missing", "income_missing", "rent_missing"]


def rmse(actual, predicted):
    if not actual or len(actual) != len(predicted):
        raise ValueError("RMSE requires aligned nonempty arrays")
    return math.sqrt(statistics.mean((a - p) ** 2 for a, p in zip(actual, predicted)))


def _ranks(values):
    return [1 + sum(other < value for other in values) + (sum(other == value for other in values) - 1) / 2
            for value in values]


def spearman(actual, predicted):
    if len(actual) < 2 or len(actual) != len(predicted):
        return None
    a, b = _ranks(actual), _ranks(predicted)
    am, bm = statistics.mean(a), statistics.mean(b)
    denominator = math.sqrt(sum((v - am) ** 2 for v in a) * sum((v - bm) ** 2 for v in b))
    return sum((x - am) * (y - bm) for x, y in zip(a, b)) / denominator if denominator else None


def category_normalization(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["reviews_count"] is not None:
            groups[row["primary_category"]].append(math.log1p(row["reviews_count"]))
    return {c: {"mean": statistics.mean(values), "std": statistics.pstdev(values) or 1.0}
            for c, values in groups.items()}


def evaluate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["category_id"]].append(row)
    metrics = {}
    for category, group in sorted(groups.items()):
        actual = [r["actual_log_count"] for r in group]
        predicted = [r["prediction_log_count"] for r in group]
        metrics[category] = {"n": len(group), "log_count_rmse": rmse(actual, predicted),
                             "spearman": spearman(actual, predicted)}
    return {"categories": metrics, "macro_rmse": statistics.mean(m["log_count_rmse"] for m in metrics.values()) if metrics else None}


def audit_features(directory, tables, report):
    reproduction = f"python -m tests.integration.verify_release --artifact-dir '{directory}'"
    if not {"place_features", "parcel_features", "places"} <= tables.keys():
        report.add("model.features", "BLOCKED", "Data", "Canonical feature/source tables are absent or invalid.",
                   reproduction, "Matching training and parcel feature exports.")
        return
    try:
        definitions = read_json(Path(directory) / "feature_definitions.json")
        names = definitions["feature_names"]
        if len(names) != len(set(names)) or set(PREDICTORS) - set(names):
            raise ValueError(f"Unique ordered predictors must include {PREDICTORS}")
        if any(any(token in name.lower() for token in ("review", "rating", "popularity", "target")) for name in names):
            raise ValueError("Review-derived or target-derived predictor name")
        if definitions.get("distance_crs") != "EPSG:26910":
            raise ValueError("Feature distances must use EPSG:26910")
        for table in ("place_features", "parcel_features"):
            for row in tables[table]:
                if [key for key in row if key in names] != names:
                    raise ValueError(f"Predictor names/order differ in {table}")
                for value, missing in (("income_usd", "income_missing"), ("rent_usd", "rent_missing"),
                                       ("population_density_per_km2", "density_missing")):
                    if row[missing] is not (row[value] is None):
                        raise ValueError(f"Missingness flag does not match {value}")
                    if not finite(row[value], low=0, nullable=True):
                        raise ValueError(f"Invalid numeric predictor {value}")
        from pyproj import Transformer
        project = Transformer.from_crs(4326, 26910, always_xy=True).transform
        places = {r["place_id"]: r for r in tables["places"]}
        xy = {key: project(row["longitude"], row["latitude"]) for key, row in places.items()}
        supply = {key: row for key, row in places.items() if row["closure_status"] in {"open", "unknown"}}
        focal_rows = [(r, r["place_id"], xy[r["place_id"]]) for r in tables["place_features"]]
        if "parcels" not in tables:
            raise ValueError("Parcel geometry is required to verify parcel feature parity")
        manifest = read_json(Path(directory) / "source_manifest.json")
        parcel_path = Path(directory) / manifest["artifacts"]["parcels"]["file"]
        _, geo = _read_table(parcel_path)
        crs = geo.get("columns", {}).get("geometry", {}).get("crs", "EPSG:4326") if geo else manifest["geometry_crs"]["parcels"]
        parcel_xy = {}
        for parcel in tables["parcels"]:
            _, point = _geometry(parcel["representative_point"], crs, polygon=False)
            parcel_xy[parcel["parcel_id"]] = (point.x, point.y)
        focal_rows += [(r, None, parcel_xy[r["parcel_id"]]) for r in tables["parcel_features"]]
        for row, focal, origin in focal_rows:
            distances = {key: math.dist(origin, xy[key]) for key in supply if key != focal}
            is_peer = lambda key: row["category_id"] in {supply[key]["primary_category"], *supply[key]["service_tags"]}
            for radius in (400, 800, 1600):
                expected = {
                    f"peer_count_{radius}m": sum(d <= radius and is_peer(key) for key, d in distances.items()),
                    f"other_service_count_{radius}m": sum(d <= radius and not is_peer(key) for key, d in distances.items()),
                }
                if radius <= 800:
                    expected[f"transit_count_{radius}m"] = sum(d <= radius and "transit" in {
                        supply[key]["primary_category"], *supply[key]["service_tags"]} for key, d in distances.items())
                if any(row.get(k) != v for k, v in expected.items()):
                    raise ValueError(f"Spatial counts mismatch for {focal} at {radius}m; focal/closure exclusion or tags differ")
            nearest = min((d for key, d in distances.items() if is_peer(key)), default=None)
            if nearest is None and row["nearest_peer_m"] is not None or nearest is not None and (
                not finite(row["nearest_peer_m"], low=0) or not math.isclose(row["nearest_peer_m"], nearest, abs_tol=0.01)
            ):
                raise ValueError(f"Nearest-peer distance mismatch for {focal}")
        report.add("model.features", "PASS", "Data", f"Predictor order, missingness, spatial parity and focal/closure exclusion checked for {len(focal_rows)} rows.",
                   reproduction, "No review-derived predictors; independently projected neighbor features.", impact="model integrity")
    except Exception as exc:
        report.add("model.features", "FAIL", "Data", exc, reproduction,
                   "Versioned ordered feature definitions and reproducible focal-excluded features.")


def audit_model(directory, tables, report: Report):
    directory = Path(directory)
    reproduction = f"python -m tests.integration.verify_release --artifact-dir '{directory}'"
    audit_features(directory, tables, report)
    try:
        manifest = read_json(directory / "model_manifest.json")
        path = next((p for p in (directory / "predictions.parquet", directory / "predictions.json") if p.exists()), None)
        if path is None or "places" not in tables or "place_features" not in tables:
            raise FileNotFoundError("Frozen predictions and canonical training sources are required")
        if report.mode == "release" and path.suffix == ".json":
            raise ValueError("Release predictions must use Parquet")
        predictions, _ = _read_table(path)
        if not predictions:
            raise ValueError("Predictions export is empty")
        places = {r["place_id"]: r for r in tables["places"] if r["reviews_count"] is not None}
        features = {r["place_id"]: r for r in tables["place_features"]}
        expected_split = {"Oakland": "train", "Berkeley": "validation", "San Francisco": "test"}
        counts = Counter((r["city"], r["primary_category"]) for key, r in places.items() if key in features)
        supported = {c for c in CATEGORIES if counts["Oakland", c] >= 100 and counts["Berkeley", c] >= 30}
        if set(manifest["supported_model_categories"]) != supported:
            raise ValueError("Model support categories violate 100 Oakland / 30 Berkeley minimums")
        if supported and not manifest.get("model_attempted"):
            raise ValueError("Supported categories require a model trial before choosing a baseline")
        if manifest.get("model_attempted"):
            estimator = manifest["estimator"]
            parameters = estimator["parameters"]
            fixed = {"learning_rate": 0.05, "max_iter": 200, "min_samples_leaf": 20,
                     "early_stopping": False, "random_state": 42}
            if estimator["name"] != "HistGradientBoostingRegressor" or any(parameters.get(k) != v for k, v in fixed.items()):
                raise ValueError("Estimator configuration differs from the fixed model recipe")
            if parameters.get("max_leaf_nodes") not in {15, 31} or parameters.get("l2_regularization") not in {0, 1}:
                raise ValueError("Estimator leaves/L2 outside the agreed search")
            if {(r["max_leaf_nodes"], r["l2_regularization"]) for r in manifest["selection_trials"]} != {(15, 0), (15, 1), (31, 0), (31, 1)}:
                raise ValueError("Record all four Berkeley model-selection trials")
        for key, row in features.items():
            if key not in places:
                raise ValueError(f"Unlabeled or unmatched training row {key}")
            source = places[key]
            if row["split"] != expected_split.get(source["city"]):
                raise ValueError(f"Geographic split violation at {key}")
            if not finite(row["target"]) or not math.isclose(row["target"], math.log1p(source["reviews_count"]), abs_tol=1e-10):
                raise ValueError(f"Target must be log1p(Places.reviews_count), not joined sampled reviews: {key}")
        output = {}
        for stage, fit_cities, eval_city in (("selection", {"Oakland"}, "Berkeley"),
                                             ("final", {"Oakland", "Berkeley"}, "San Francisco")):
            fit = [places[key] for key in features if places[key]["city"] in fit_cities]
            fit_ids = {r["place_id"] for r in fit}
            exported_ids = manifest["fit_place_ids"][stage]
            if len(exported_ids) != len(set(exported_ids)) or set(exported_ids) != fit_ids:
                raise ValueError(f"{stage} fit IDs must contain exactly the permitted training cities")
            norm = category_normalization(fit)
            if set(manifest["normalization"][stage]) != set(norm):
                raise ValueError(f"{stage} normalization category coverage differs")
            for cat, values in norm.items():
                if any(not math.isclose(manifest["normalization"][stage][cat][stat], value, abs_tol=1e-10)
                       for stat, value in values.items()):
                    raise ValueError(f"{stage}/{cat} normalization not fitted on training rows only")
            for field in ("income_usd", "population_density_per_km2"):
                values = [features[key][field] for key in fit_ids if features[key][field] is not None]
                expected = statistics.median(values) if values else None
                if manifest["slice_thresholds"][stage][field] != expected:
                    raise ValueError(f"{stage}/{field} slice threshold is not the training median")
            category_baselines = {cat: statistics.median(math.log1p(r["reviews_count"]) for r in fit if r["primary_category"] == cat)
                                  for cat in norm}
            city_baselines = {(cat, city): statistics.median(math.log1p(r["reviews_count"]) for r in fit
                              if r["primary_category"] == cat and r["city"] == city)
                              for cat in norm for city in fit_cities
                              if any(r["primary_category"] == cat and r["city"] == city for r in fit)}
            by_approach = defaultdict(list)
            seen = set()
            for row in (r for r in predictions if r["stage"] == stage):
                key, approach = row["place_id"], row["approach"]
                if key not in features or places[key]["city"] != eval_city:
                    raise ValueError(f"{stage} prediction includes wrong evaluation city or unknown ID")
                if (key, approach) in seen:
                    raise ValueError("Duplicate evaluation row")
                seen.add((key, approach))
                source = places[key]
                if row["category_id"] != source["primary_category"] or row["city"] != source["city"]:
                    raise ValueError("Prediction join changes category or city")
                actual = math.log1p(source["reviews_count"])
                if not finite(row["actual_log_count"]) or not math.isclose(row["actual_log_count"], actual, abs_tol=1e-10):
                    raise ValueError("Exported actual differs from Places target")
                if approach not in {"model", "category_baseline", "category_city_baseline"}:
                    raise ValueError("Unknown prediction approach")
                pred = row["prediction_log_count"]
                if not finite(pred, nullable=True):
                    raise ValueError("Prediction must be finite or explicitly null")
                if approach == "model" and (pred is not None) != (row["category_id"] in supported):
                    raise ValueError("Model predictions must be null exactly for unsupported categories")
                baseline = category_baselines.get(row["category_id"])
                if approach == "category_city_baseline":
                    baseline = city_baselines.get((row["category_id"], row["city"]), baseline)
                if approach != "model" and (baseline is None and pred is not None or baseline is not None and (
                    pred is None or not math.isclose(pred, baseline, abs_tol=1e-10)
                )):
                    raise ValueError("Baseline not fitted on training categories/cities with fallback")
                by_approach[approach].append(row)
            expected_ids = {key for key in features if places[key]["city"] == eval_city}
            approaches = {"category_baseline", "category_city_baseline"}
            if manifest.get("model_attempted"):
                approaches.add("model")
            if set(by_approach) != approaches or any({r["place_id"] for r in rows} != expected_ids for rows in by_approach.values()):
                raise ValueError(f"{stage}: approaches must export identical evaluation rows, using nulls for unsupported categories")
            common = set.intersection(*({r["place_id"] for r in rows if r["prediction_log_count"] is not None}
                                        for rows in by_approach.values()))
            metrics = {approach: evaluate([r for r in rows if r["place_id"] in common]) for approach, rows in by_approach.items()}
            selected = manifest["selected_approach"]
            if selected not in approaches:
                raise ValueError("Selected approach is unavailable")
            selected_rows = [r for r in by_approach[selected] if r["prediction_log_count"] is not None]
            thresholds = manifest["slice_thresholds"][stage]
            slices = {}
            for field, threshold in thresholds.items():
                for side in ("low", "high"):
                    subset = [r for r in selected_rows if features[r["place_id"]].get(field) is not None and threshold is not None
                              and (features[r["place_id"]][field] <= threshold) == (side == "low")]
                    slices[f"{field}:{side}"] = {"n": len(subset), "status": "insufficient" if len(subset) < 30 else "supported",
                                                  "metrics": evaluate(subset) if len(subset) >= 30 else None}
            worst = sorted(selected_rows, key=lambda r: abs(r["prediction_log_count"] - r["actual_log_count"]), reverse=True)[:10]
            output[stage] = {"evaluation_city": eval_city, "evaluation_rows": len(expected_ids), "common_rows": len(common),
                             "metrics": metrics, "slices": slices, "worst_errors": worst,
                             "coverage_by_category": dict(Counter(r["category_id"] for r in selected_rows))}
        if manifest.get("model_attempted"):
            metrics = output["selection"]["metrics"]
            model_rmse, baseline_rmse = metrics["model"]["macro_rmse"], metrics["category_baseline"]["macro_rmse"]
            passed_gate = bool(supported) and model_rmse is not None and baseline_rmse is not None and baseline_rmse > 0 and model_rmse <= 0.95 * baseline_rmse
            if (manifest["selected_approach"] == "model") != passed_gate:
                raise ValueError("Selected approach differs from >=5% Berkeley macro RMSE improvement gate")
        if manifest.get("sf_evaluation_count") != 1 or not manifest.get("estimator_version") or manifest.get("seed") != 42:
            raise ValueError("Record a single final SF evaluation, estimator version and seed 42")
        report.add("model.evaluation", "PASS", "Data", f"Recalculated selection and final metrics separately; selected {manifest['selected_approach']}. "
                   f"Common rows: Berkeley {output['selection']['common_rows']}, SF {output['final']['common_rows']}. "
                   "See model_metrics.json for per-category errors, coverage, worst errors and slices; slices <30 are insufficient.",
                   reproduction, "Correct splits, train-only transforms/baselines, common rows and model selection gate.", impact="model integrity")
        return output
    except (FileNotFoundError, StopIteration) as exc:
        report.add("model.evaluation", "BLOCKED", "Data", exc, reproduction, "Frozen predictions, feature definitions and model_manifest.json.")
    except Exception as exc:
        report.add("model.evaluation", "FAIL", "Data", exc, reproduction, "Independently reproducible exported model evaluation.", categories=CANDIDATES)
    return {}
