"""Opt-in product smoke tests; missing owners' implementations are explicit skips."""

import importlib
import importlib.util
import os
from pathlib import Path

import pytest

from .api_checks import Api, run_api_checks
from .artifacts import read_json
from .provider_checks import run_probes
from .reporting import Report


@pytest.mark.live
def test_integrated_api_contract():
    base_url = os.getenv("PARCEL_API_BASE_URL")
    if not base_url:
        pytest.skip("No running API configured: set PARCEL_API_BASE_URL including /api/v1")
    report = Report(os.getenv("PARCEL_CHECKER_MODE", "fixture"))
    path = os.getenv("PARCEL_ORACLE_CASES")
    source = os.getenv("PARCEL_SOURCE_FIXTURE")
    cases = read_json(Path(path)) if path else []
    if source:
        from .published_api import fixture_cases
        cases = fixture_cases(source)
    run_api_checks(Api(base_url), report, bbox=os.getenv("PARCEL_BBOX", "-122.43,37.76,-122.40,37.79"),
                   parcel_id=os.getenv("PARCEL_ID"), oracle_cases=cases)
    assert not report.failed, "\n".join(f"{c.id}: {c.detail}" for c in report.checks if c.status in {"FAIL", "BLOCKED"})


@pytest.mark.live
def test_production_parcel_provider_faults():
    driver_path = os.getenv("PARCEL_CHECKER_DRIVER")
    if not driver_path:
        if importlib.util.find_spec("fastapi") is None:
            pytest.skip("Install Backend locked dependencies to run the actual provider probes")
        from .production_provider_checks import run_probes as production_probes
        result = production_probes()
        assert all(c["status"] == "PASS" for c in result["checks"]), result
        return
    module, name = driver_path.rsplit(":", 1)
    assert not module.startswith("tests."), "Live checks must wrap the production adapter"
    results = run_probes(getattr(importlib.import_module(module), name))
    assert all(r["status"] == "PASS" for r in results), results
