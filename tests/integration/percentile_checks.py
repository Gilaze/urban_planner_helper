"""Audit a frozen SF reference export, separately from request-time ranking."""

import math
from pathlib import Path

from .artifacts import finite, read_json
from .oracle import empirical_percentile

METRICS = ("need", "retained_tax_usd_per_year", "operating_fte", "sustainability")


def validate_percentiles(directory, tables, report):
    reproduction = f"python -m tests.integration.verify_release --artifact-dir '{directory}'"
    try:
        data = read_json(Path(directory) / "scoring_reference.json")
        if data["method"] != "midpoint_ecdf":
            raise ValueError("Percentile convention needs Backend/Checker agreement; expected midpoint_ecdf")
        if not data["data_version"]:
            raise ValueError("Frozen references require a data version")
        rows = data["rows"]
        keys = {(r["parcel_id"], r["category_id"]) for r in rows}
        expected = {(r["parcel_id"], r["category_id"]) for r in tables.get("parcel_features", [])}
        if not rows or len(keys) != len(rows) or keys != expected:
            raise ValueError("Reference release rows must uniquely match the SF parcel/category universe")
        for metric in METRICS:
            ref = data["references"][metric]
            observed = [r["raw"][metric] for r in rows if r["raw"][metric] is not None]
            if any(not finite(x) for x in ref + observed) or sorted(ref) != sorted(observed):
                raise ValueError(f"{metric}: reference differs from all eligible release values")
        for row in rows:
            scores = {metric: empirical_percentile(row["raw"][metric], data["references"][metric])
                      if row["raw"][metric] is not None else None for metric in METRICS}
            expected_scores = {"need": scores["need"], "sustainability": scores["sustainability"],
                "economic": (scores["retained_tax_usd_per_year"] + scores["operating_fte"]) / 2
                if scores["retained_tax_usd_per_year"] is not None and scores["operating_fte"] is not None else None}
            for metric, expected_score in expected_scores.items():
                actual = row["normalized"][metric]
                if (expected_score is None and actual is not None or expected_score is not None and
                    (not finite(actual) or not math.isclose(actual, expected_score, abs_tol=1e-8))):
                    raise ValueError(f"{row['parcel_id']}/{row['category_id']}: normalized {metric} differs")
        report.add("scoring.frozen_percentiles", "PASS", "Backend", f"Checked {len(rows)} frozen SF scenario rows, midpoint ties and economic component averaging.",
                   reproduction, "One versioned fixed reference universe; missing economic components propagate null.", impact="scoring integrity")
    except FileNotFoundError as exc:
        report.add("scoring.frozen_percentiles", "BLOCKED", "Backend", exc, reproduction, "scoring_reference.json export; agree its convention at the contract handoff.")
    except Exception as exc:
        report.add("scoring.frozen_percentiles", "FAIL", "Backend", exc, reproduction, "Correct release-wide percentile references and normalized scores.")
