import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from .api_checks import Api, run_api_checks
from .percentile_checks import validate_percentiles
from .reporting import Report
from .verify_release import main, browser_evidence_passed
from .artifacts import validate_artifacts

FIXTURES = Path(__file__).parent / "fixtures"


def test_http_client_parses_structured_error(monkeypatch):
    def reject(request, timeout):
        assert timeout == 5 and request.method == "POST"
        raise HTTPError(request.full_url, 422, "bad request", {"Content-Type": "application/json"}, io.BytesIO(b'{"detail":"invalid"}'))
    monkeypatch.setattr("tests.integration.api_checks.urlopen", reject)
    status, payload, elapsed = Api("http://localhost/api/v1").request("POST", "/scenarios/compare", {"parcel_id": "sf:1"})
    assert status == 422 and payload == {"detail": "invalid"} and elapsed >= 0


@pytest.mark.parametrize("raw,content_type,error", [(b"<html>failed</html>", "text/html", "not labeled JSON"),
                                                   (b'{"value":NaN}', "application/json", "Non-JSON")])
def test_http_client_rejects_non_json_or_nonfinite(monkeypatch, raw, content_type, error):
    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": content_type}
    monkeypatch.setattr("tests.integration.api_checks.urlopen", lambda *a, **kw: Response(raw))
    with pytest.raises(ValueError, match=error):
        Api("http://localhost/api/v1").request("GET", "/health")


def test_offline_api_creates_failure_and_blocks_dependent_probes(monkeypatch):
    def offline(*args, **kwargs):
        raise URLError("controlled offline test")
    monkeypatch.setattr("tests.integration.api_checks.urlopen", offline)
    report = Report("fixture")
    run_api_checks(Api("http://localhost/api/v1"), report)
    assert [(r.id, r.status) for r in report.checks] == [("api.health", "FAIL"), ("api.remaining", "BLOCKED")]


def test_frozen_percentiles_and_economic_averaging():
    report = Report("fixture")
    tables = validate_artifacts(FIXTURES / "artifacts", report)
    validate_percentiles(FIXTURES / "artifacts", tables, report)
    assert not report.failed, [(c.id, c.detail) for c in report.checks if c.status == "FAIL"]


def test_cli_missing_owners_is_nonzero_and_does_not_sign_off(tmp_path):
    assert main(["--report-dir", str(tmp_path)]) == 1
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["release_accepted"] is False
    for owner in ("Backend", "Data", "Frontend + Checker", "Backend + Checker", "Checker"):
        assert any(c["owner"] == owner and c["status"] == "BLOCKED" for c in result["checks"])


def test_fixture_report_records_measured_metrics_without_claiming_release(tmp_path):
    assert main(["--mode", "fixture", "--artifact-dir", str(FIXTURES / "artifacts"), "--report-dir", str(tmp_path)]) == 1
    result = json.loads((tmp_path / "results.json").read_text())
    assert not any(c["status"] == "FAIL" for c in result["checks"])
    assert result["release_accepted"] is False
    assert json.loads((tmp_path / "model_metrics.json").read_text())["final"]["evaluation_rows"] == 5
    report = (tmp_path / "acceptance.md").read_text()
    assert "Synthetic checker fixtures only" in report and "Reproduce:" in report


def test_empty_or_fixture_report_never_accepts_release(tmp_path):
    assert Report().write(tmp_path / "empty")["release_accepted"] is False
    report = Report("fixture")
    report.add("synthetic", "PASS", "Checker", "test", "test", "test")
    assert report.write(tmp_path / "fixture")["release_accepted"] is False


def test_unrelated_browser_results_cannot_satisfy_release_gate():
    data = {"stats": {"expected": 16, "unexpected": 0, "flaky": 0, "skipped": 0}, "errors": [],
            "config": {"metadata": {"target": "integrated-product"}}, "suites": []}
    assert browser_evidence_passed(data) is False


def test_pipeline_evidence_must_be_complete_and_current(tmp_path):
    from .pipeline_artifacts import sha256
    root = Path(__file__).resolve().parents[2]
    sources = ("pipeline/src/parcel_pipeline/model.py", "pipeline/src/parcel_pipeline/features.py",
               "pipeline/src/parcel_pipeline/cli.py", "pipeline/src/parcel_pipeline/sources/sf.py", "pipeline/src/parcel_pipeline/settings.py",
               "pipeline/src/parcel_pipeline/sources/census.py", "pipeline/uv.lock", "config/categories.yaml")
    evidence = {"source_sha256": {p: sha256(root / p) for p in sources}, "checks": []}
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(evidence))
    output = tmp_path / "report"
    assert main(["--pipeline-probe-results", str(path), "--report-dir", str(output)]) == 1
    result = json.loads((output / "results.json").read_text())
    assert any(c["id"] == "release.pipeline_probes" and c["status"] == "FAIL" and "incomplete" in c["detail"] for c in result["checks"])


def test_imported_pipeline_failure_cannot_be_hidden(tmp_path):
    from .pipeline_artifacts import sha256
    root = Path(__file__).resolve().parents[2]
    sources = ("pipeline/src/parcel_pipeline/model.py", "pipeline/src/parcel_pipeline/features.py",
               "pipeline/src/parcel_pipeline/cli.py", "pipeline/src/parcel_pipeline/sources/sf.py", "pipeline/src/parcel_pipeline/settings.py",
               "pipeline/src/parcel_pipeline/sources/census.py", "pipeline/uv.lock", "config/categories.yaml")
    ids = ("pipeline.spatial_features", "pipeline.train_normalization", "pipeline.sf_label_isolation",
           "pipeline.serving_fallback_evaluation", "pipeline.partial_assessment", "pipeline.publication_fingerprint",
           "pipeline.dotenv_precedence", "pipeline.census_error_privacy")
    checks = [{"id": id, "status": "FAIL" if id == "pipeline.sf_label_isolation" else "PASS", "owner": "Data",
               "detail": "controlled regression", "reproduction": "test", "expected": "no regression", "impact": "release blocker"} for id in ids]
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"source_sha256": {p: sha256(root / p) for p in sources}, "checks": checks}))
    output = tmp_path / "report"
    assert main(["--pipeline-probe-results", str(path), "--report-dir", str(output)]) == 1
    result = json.loads((output / "results.json").read_text())
    assert result["release_accepted"] is False and any(c["id"] == "pipeline.sf_label_isolation" and c["status"] == "FAIL" for c in result["checks"])
