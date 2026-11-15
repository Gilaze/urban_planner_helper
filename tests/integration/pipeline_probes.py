"""Independent production-pipeline probes using small synthetic inputs.

Run with the pipeline's locked Python 3.11 interpreter. These probes inspect
runtime behavior without retraining on or revisiting the actual SF test data.
Failures belong to the Data owner; the checker does not change product code.
"""

import argparse
import hashlib
import json
import math
import platform
import os
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def run_probes():
    import pandas as pd
    from pyproj import Transformer
    from parcel_pipeline.features import FEATURE_COLUMNS, SupplyIndex, spatial_features
    from parcel_pipeline.model import ReviewActivityModel, train_and_evaluate, predict_parcels
    from parcel_pipeline.sources import sf
    from parcel_pipeline.cli import publish
    from parcel_pipeline.settings import load_dotenv
    from parcel_pipeline.sources.census import fetch_acs

    checks = []
    command = "pipeline/.venv/bin/python -m tests.integration.pipeline_probes"
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    def check(id, action, expected, categories=()):
        try:
            detail = action()
            status = "PASS"
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        checks.append({"id": id, "status": status, "owner": "Data", "detail": detail,
                       "reproduction": command, "expected": expected,
                       "impact": "release blocker", "categories": list(categories)})

    def sample_rows(spec):
        rows = []
        for city, count in (("Oakland", spec[0]), ("Berkeley", spec[1]), ("San Francisco", spec[2])):
            for category in spec[3]:
                for i in range(count):
                    target = 1 + (i % 40) * .05 if category == "clinic" else 2.0
                    rows.append({**dict.fromkeys(FEATURE_COLUMNS, 0.0), "place_id": f"{city}-{category}-{i}",
                                 "city": city, "category": category, "category_code": 0 if category == "clinic" else 1,
                                 "split": {"Oakland": "train", "Berkeley": "validation", "San Francisco": "test"}[city],
                                 "same_category_count_400m": i % 40, "target_log1p_reviews_count": target,
                                 "reviews_count": math.expm1(target), "labeled": True})
        return pd.DataFrame(rows)

    def spatial():
        inverse = Transformer.from_crs(26910, 4326, always_xy=True).transform
        rows = []
        for id, meters, tags, current in (("focal", 0, ["clinic"], True), ("near", 300, ["clinic"], True),
                                         ("mid", 700, ["clinic"], True), ("far", 1500, ["clinic"], True),
                                         ("closed", 10, ["clinic"], False), ("grocery", 100, ["grocery"], True),
                                         ("transit", 450, ["transit"], True)):
            lon, lat = inverse(565000 + meters, 4184000)
            rows.append({"place_id": id, "lon": lon, "lat": lat, "service_tags": tags, "is_current_supply": current})
        lon, lat = inverse(565000, 4184000)
        output = spatial_features(pd.DataFrame({"lon": [lon], "lat": [lat], "focal_place_id": ["focal"]}),
                                  "clinic", SupplyIndex(pd.DataFrame(rows), ("clinic", "grocery")), None).iloc[0]
        require([output[f"same_category_count_{r}m"] for r in (400, 800, 1600)] == [1, 2, 3], "Peer count/focal/closure exclusion differs")
        require(output["other_service_count_800m"] == 1 and output["transit_count_800m"] == 1,
                "Transit was double-counted as another service")
        require(math.isclose(output["nearest_peer_distance_m"], 300, abs_tol=.001), "Nearest peer differs from hand distance")
        require(not any(any(t in c.lower() for t in ("review", "rating", "popularity", "target")) for c in FEATURE_COLUMNS), "Target-derived predictor found")
        return "Hand distances 300/700/1500m, closure/focal exclusion, transit complement and predictor names agree."

    def dotenv_precedence():
        with TemporaryDirectory(prefix="parcel-env-probe-") as directory:
            path = Path(directory) / ".env"
            path.write_text("CENSUS_API_KEY=synthetic-file-value\nPARCEL_CHECKER_ENV_PROBE=first\nexport PARCEL_CHECKER_ENV_PROBE='last'\n")
            with patch.dict(os.environ, {"CENSUS_API_KEY": "synthetic-existing"}):
                os.environ.pop("PARCEL_CHECKER_ENV_PROBE", None)
                load_dotenv(path)
                require(os.environ["CENSUS_API_KEY"] == "synthetic-existing" and os.environ["PARCEL_CHECKER_ENV_PROBE"] == "last",
                        "Explicit environment must override dotenv; last duplicate dotenv line must win")
        return "Synthetic environment override, duplicate-line precedence and export/quote parsing verified without disclosing credentials."

    def census_error_privacy():
        import requests
        secret = "synthetic-checker-key-do-not-log"
        for mode in ("network", "http", "json"):
            class Session:
                def get(self, url, params, timeout):
                    if mode == "network":
                        raise requests.ConnectionError(f"failed https://checker.invalid?key={secret}")
                    def decode():
                        raise ValueError(f"bad body for key={secret}")
                    return SimpleNamespace(status_code=403 if mode == "http" else 200,
                                           url=f"https://checker.invalid?key={secret}", json=decode)
            try:
                fetch_acs("tract", secret, session=Session())
            except RuntimeError as error:
                rendered = "".join(traceback.format_exception(error))
                require(secret not in rendered and "checker.invalid" not in rendered, "Census failure exposes credential-bearing request URL")
            else:
                raise ValueError("Controlled failing Census request was accepted")
        return "Network, HTTP and non-JSON Census failures omit credential-bearing URLs, including formatted traceback context."

    def normalization():
        frame = sample_rows((40, 0, 0, ("clinic",)))
        frame["target_log1p_reviews_count"] = [0.0, math.log(4)] * 20
        model = ReviewActivityModel({"max_leaf_nodes": 15, "l2_regularization": 0}, ["clinic"]).fit(frame)
        mean, std = model.norm["clinic"]
        require(math.isclose(mean, math.log(2), abs_tol=1e-12) and math.isclose(std, math.log(2), abs_tol=1e-12),
                "Training population mean/std differ from hand calculation")
        return "40 Oakland-only synthetic targets: mean=std=log(2); estimator normalization agrees."

    def sf_label_isolation():
        frame = sample_rows((3, 3, 3, ("school",)))
        frame["target_log1p_reviews_count"] = 0.0
        frame["reviews_count"] = 0.0
        original = train_and_evaluate(frame, ["school"])["report"]
        changed = frame.copy()
        changed.loc[changed["split"] == "test", "target_log1p_reviews_count"] = math.log(2)
        changed.loc[changed["split"] == "test", "reviews_count"] = 1
        other = train_and_evaluate(changed, ["school"])["report"]
        a, b = original["labeled_counts"]["school"]["train"], other["labeled_counts"]["school"]["train"]
        require(a == b and original["degenerate_target_categories"] == other["degenerate_target_categories"],
                f"Changing only synthetic SF labels changed Oakland training eligibility: {a} -> {b}; "
                f"degenerate categories {original['degenerate_target_categories']} -> {other['degenerate_target_categories']}")
        return "SF labels cannot affect pre-test category eligibility."

    def serving_fallback_evaluation():
        frame = pd.concat([sample_rows((120, 40, 40, ("clinic",))), sample_rows((3, 3, 3, ("pharmacy",)))], ignore_index=True)
        result = train_and_evaluate(frame, ["clinic", "pharmacy"])
        require(result["selected"] == "model", "Synthetic strong-signal setup did not select a model")
        query = frame.loc[(frame["category"] == "pharmacy") & (frame["split"] == "test"), FEATURE_COLUMNS].head(1).copy()
        query["category"], query["parcel_id"] = "pharmacy", "sf:checker"
        served = predict_parcels(query, result).iloc[0]
        metrics = result["report"]["test"]["selected"]["pharmacy"]
        require(served["source"] == "category_baseline" and math.isclose(served["predicted_log1p_reviews_count"], 2), "Synthetic pharmacy baseline serving setup differs")
        require(metrics["n_predicted"] == metrics["n"] == 3,
                f"Production serves pharmacy category baseline=2, but selected SF evaluation has {metrics['n_predicted']}/{metrics['n']} predictions")
        return "Evaluation and served unsupported-category fallback use identical rows/predictions."

    def lots_fixture():
        directory = ROOT / "pipeline/tests/fixtures"
        load = lambda name: json.loads((directory / name).read_text())
        parcels = sf.parse_parcel_records(load("sf_parcels_api.json"))
        lots, _ = sf.build_physical_parcels(parcels, sf.parse_landuse_records(load("sf_landuse_api.json")),
                    sf.parse_assessor_records(load("sf_assessor_api.json"), "2025"), "synthetic",
                    current_landuse=sf.parse_current_landuse_records(load("sf_landuse_current_api.json")))
        return lots

    def partial_assessment():
        lots = lots_fixture().set_index("parcel_id")
        row = lots.loc["sf:0025032"]
        require(pd.isna(row["assessed_improvement_usd"]),
                f"Two condo APNs include an unknown improvement value, but the parcel reports a complete USD {row['assessed_improvement_usd']:g} total with no partial-value label")
        return "Unknown condo member assessment propagates null to the physical-lot total."

    def publication_fingerprint():
        lots = lots_fixture()
        with TemporaryDirectory(prefix="parcel-publish-probe-") as directory:
            root = Path(directory)
            release = root / "artifacts/release"
            release.mkdir(parents=True)
            lots.to_parquet(release / "parcels.geoparquet", index=False)
            pd.DataFrame({"place_id": ["synthetic"]}).to_parquet(release / "places.parquet", index=False)
            pd.DataFrame({"parcel_id": lots["parcel_id"], "category_id": "clinic"}).to_parquet(release / "review_predictions.parquet", index=False)
            (release / "backend_manifest.json").write_text("{}")
            before = {name: {"sha256": hashlib.sha256((release / name).read_bytes()).hexdigest(), "rows": len(lots)}
                      for name in ("parcels.geoparquet", "review_predictions.parquet")}
            (release / "source_manifest.json").write_text(json.dumps({"outputs": before, "census_skipped": True}))
            out = root / "published"
            publish(SimpleNamespace(artifacts_dir=root / "artifacts"), out, allow_missing_census=True)
            manifest = json.loads((out / "source_manifest.json").read_text())
            actual = hashlib.sha256((out / "parcels.geoparquet").read_bytes()).hexdigest()
            declared = manifest["outputs"]["parcels.geoparquet"]["sha256"]
            require(actual == declared, "publish rewrites/filters parcels.geoparquet but copies the canonical checksum unchanged; published manifest does not fingerprint the served file")
        return "Published-file fingerprints match the rewritten projection."

    for id, action, expected, categories in (
        ("pipeline.dotenv_precedence", dotenv_precedence, "Existing environment wins; later dotenv duplicates win without logging values.", ()),
        ("pipeline.census_error_privacy", census_error_privacy, "Request failures never expose the Census key or request URL.", ()),
        ("pipeline.spatial_features", spatial, "Focal/closure exclusion and hand-calculated metric radii.", ("clinic", "grocery")),
        ("pipeline.train_normalization", normalization, "Train-only mean/std and zero-variance convention.", ("clinic",)),
        ("pipeline.sf_label_isolation", sf_label_isolation, "Changing SF test labels never changes Oakland/Berkeley training eligibility.", ("school",)),
        ("pipeline.serving_fallback_evaluation", serving_fallback_evaluation, "Evaluation includes the baseline fallback actually served for unsupported categories.", ("pharmacy",)),
        ("pipeline.partial_assessment", partial_assessment, "Unknown APN values are null at physical-lot grain, or explicitly labeled partial subtotals.", ()),
        ("pipeline.publication_fingerprint", publication_fingerprint, "Published manifests contain hashes and row counts for the published bytes.", ()),
    ):
        check(id, action, expected, categories)
    files = ("pipeline/src/parcel_pipeline/model.py", "pipeline/src/parcel_pipeline/features.py",
             "pipeline/src/parcel_pipeline/cli.py", "pipeline/src/parcel_pipeline/sources/sf.py", "pipeline/src/parcel_pipeline/settings.py",
             "pipeline/src/parcel_pipeline/sources/census.py", "pipeline/uv.lock", "config/categories.yaml")
    return {"mode": "production-code-synthetic-inputs", "python_version": platform.python_version(), "checks": checks,
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/verification/pipeline-probe-results.json"))
    args = parser.parse_args(argv)
    result = run_probes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 1 if any(c["status"] == "FAIL" for c in result["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
