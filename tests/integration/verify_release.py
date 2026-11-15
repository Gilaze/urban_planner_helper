"""python -m tests.integration.verify_release --help"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .api_checks import Api, run_api_checks
from .artifacts import read_json, validate_artifacts
from .model_audit import audit_model
from .percentile_checks import validate_percentiles
from .reporting import Report


def browser_evidence_passed(data):
    titles = {"parcel selection, preview and reset", "keyboard parcel selection", "stale snapshot label",
              "unavailable API preserves usable controls", "late comparison cannot replace current selection",
              "missing basemap preserves list and comparison", "invalid geometry uses outline fallback",
              "unavailable metric shows reason and raw values"}
    projects = {"desktop-1440", "laptop-1280"}
    observed = set()
    def walk(suites):
        for suite in suites:
            for spec in suite.get("specs", []):
                if Path(spec.get("file", "")).name != "parcel-flow.spec.ts":
                    continue
                for test in spec.get("tests", []):
                    if (test.get("status") == "expected" and test.get("expectedStatus") == "passed"
                            and test.get("results") and all(r.get("status") == "passed" for r in test["results"])):
                        observed.add((spec["title"], test["projectName"]))
            walk(suite.get("suites", []))
    walk(data["suites"])
    stats = data["stats"]
    return (stats["expected"] >= 16 and stats["unexpected"] == stats["flaky"] == stats["skipped"] == 0
            and not data.get("errors") and data["config"].get("metadata", {}).get("target") == "integrated-product"
            and {(title, project) for title in titles for project in projects} <= observed)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent Parcel Planner verification. Omitted release gates remain BLOCKED.")
    parser.add_argument("--api-base-url", help="Include /api/v1, e.g. http://127.0.0.1:8000/api/v1")
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--report-dir", type=Path, default=Path("reports/verification/latest"))
    parser.add_argument("--mode", choices=("fixture", "release"), default="release")
    parser.add_argument("--bbox", default="-122.43,37.76,-122.40,37.79")
    parser.add_argument("--parcel-id")
    parser.add_argument("--oracle-cases", type=Path, help="Only use cases actually loaded by the backend under test")
    parser.add_argument("--browser-results", type=Path, help="Playwright JSON report from the real app")
    parser.add_argument("--provider-results", type=Path, help="JSON report from the production provider probe driver")
    parser.add_argument("--manual-evidence", type=Path, help="Presentation-machine observations; see README")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    report = Report(args.mode)
    reproduction = "python -m tests.integration.verify_release"
    try:
        result = subprocess.run([sys.executable, "-m", "pytest", "-c", str(Path(__file__).with_name("pytest.ini")),
                                 str(Path(__file__).with_name("test_oracle.py")), "-q"], capture_output=True, text=True, timeout=30)
        report.add("checker.hand_calculations", "PASS" if result.returncode == 0 else "FAIL", "Checker",
                   result.stdout.strip() + result.stderr.strip(), reproduction,
                   "All independent hand-calculated scoring oracle checks pass.", impact="checker integrity")
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("checker.hand_calculations", "BLOCKED", "Checker", exc, reproduction, "Install the pinned test dependencies and run pytest.")
    if args.artifact_dir:
        tables = validate_artifacts(args.artifact_dir, report)
        metrics = audit_model(args.artifact_dir, tables, report)
        validate_percentiles(args.artifact_dir, tables, report)
        args.report_dir.mkdir(parents=True, exist_ok=True)
        (args.report_dir / "model_metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n")
    else:
        report.add("release.artifacts", "BLOCKED", "Data", "No artifact directory supplied.", reproduction, "Canonical data and model exports.")
    if args.api_base_url:
        try:
            cases = read_json(args.oracle_cases) if args.oracle_cases else []
            run_api_checks(Api(args.api_base_url, args.timeout), report, bbox=args.bbox, parcel_id=args.parcel_id, oracle_cases=cases)
            if not cases:
                report.add("api.oracle_cases", "BLOCKED", "Backend + Checker", "No matching backend-loaded oracle cases supplied.", reproduction,
                           "At least one hand-calculated raw need/travel scenario checked over HTTP.")
        except (ValueError, OSError, TypeError, KeyError) as exc:
            report.add("api.configuration", "FAIL", "Checker", exc, reproduction, "Valid API URL and oracle case file.")
    else:
        report.add("release.api", "BLOCKED", "Backend", "No API base URL supplied.", reproduction, "Ready fixture/live API.")
    for kind, path, owner in (("browser", args.browser_results, "Frontend + Checker"),
                               ("provider", args.provider_results, "Backend + Checker")):
        try:
            if path is None:
                raise FileNotFoundError(f"No {kind} results supplied")
            data = read_json(path)
            if kind == "browser":
                # Native Playwright JSON; skipped tests never count as acceptance.
                ok = browser_evidence_passed(data)
            else:
                required = {"pagination", "malformed", "timeout_fallback", "unavailable", "changed_geometry", "bounded_retry"}
                checks = {c["id"]: c["status"] for c in data["checks"]}
                ok = data.get("production_driver") is True and required <= checks.keys() and all(checks[k] == "PASS" for k in required)
            report.add(f"release.{kind}", "PASS" if ok else "FAIL", owner, f"Imported {path}; acceptance={ok}.", reproduction,
                       "All required product checks pass, without skips or checker-only stand-ins.")
        except Exception as exc:
            report.add(f"release.{kind}", "BLOCKED", owner, exc, reproduction, f"Measured {kind} checks against the integrated product.")
    try:
        if args.manual_evidence is None:
            raise FileNotFoundError("No presentation-machine evidence supplied")
        evidence = read_json(args.manual_evidence)
        required = {"privacy", "attribution", "zoning_language", "vacancy_label", "economic_scenarios",
                    "boundary_coverage", "category_audit", "sf_evaluation", "payload_agreement", "geometry_clipping"}
        observations = evidence["observations"]
        ok = (bool(evidence.get("machine")) and bool(evidence.get("observed_at")) and bool(evidence.get("reviewer"))
              and required <= observations.keys() and all(observations[k].get("status") == "PASS" and observations[k].get("evidence") for k in required))
        report.add("release.presentation_review", "PASS" if ok else "FAIL", "Checker", f"Evidence file {args.manual_evidence}; acceptance={bool(ok)}.",
                   reproduction, "Recorded visual/source review on the presentation machine with inspectable evidence.")
    except Exception as exc:
        report.add("release.presentation_review", "BLOCKED", "Checker", exc, reproduction, "Completed presentation-machine evidence; see manual_evidence.example.json.")
    payload = report.write(args.report_dir)
    counts = {status: sum(c.status == status for c in report.checks) for status in ("PASS", "FAIL", "BLOCKED", "WARN")}
    print(json.dumps({"mode": args.mode, "release_accepted": payload["release_accepted"], "counts": counts,
                      "report": str(args.report_dir / "acceptance.md")}, indent=2))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
