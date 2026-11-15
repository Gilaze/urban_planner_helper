"""Fault-inject the real DataSF parser/cache and FastAPI routes.

MockTransport replaces only the upstream network. Pagination, parsing, retry,
cache freshness, release matching and API responses are production code. All
source rows and the live-mode serving release in these probes are synthetic.
"""

import argparse
import copy
import hashlib
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BBOX = "-122.43,37.76,-122.40,37.79"
PARCEL = "sf:fixture-001"


def run_probes():
    import httpx
    from fastapi.testclient import TestClient
    from backend.api import create_app
    from backend.datasf import DataSF, DataSFParcels, ProviderUnavailable, collect_parcels
    from backend.economics import load_assumptions
    from backend.release import ReleaseInputs, freeze

    source = json.loads((ROOT / "contracts/fixture-inputs.json").read_text())
    geometry = source["parcels"][0]["geometry"]
    row = {"mapblklot": "fixture-001", "geography_type": "parcel", "the_geom": geometry,
           "data_as_of": "synthetic-checker-v1"}
    checks = []

    def check(id, action):
        try:
            detail = action()
            checks.append({"id": id, "status": "PASS", "owner": "Backend", "detail": detail})
        except Exception as exc:
            checks.append({"id": id, "status": "FAIL", "owner": "Backend",
                           "detail": f"{type(exc).__name__}: {exc}"})

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def provider(callback):
        client = httpx.Client(transport=httpx.MockTransport(callback), timeout=.05)
        return client, DataSFParcels(DataSF(client))

    def pagination():
        calls = []
        def reply(request):
            body = json.loads(request.content)
            calls.append(body)
            page = body["page"]["pageNumber"]
            count, offset = (1000, 0) if page == 1 else (1, 1000)
            return httpx.Response(200, json={"data": [dict(row, mapblklot=f"checker-{i:04d}") for i in range(offset, offset + count)]})
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            parsed = list(collect_parcels(DataSF(client).paginate("https://checker.invalid", "SELECT * ORDER BY mapblklot")))
        require(len(parsed) == len({p.id for p in parsed}) == 1001, "Pagination dropped or duplicated a physical lot")
        require([c["page"] for c in calls] == [{"pageNumber": 1, "pageSize": 1000}, {"pageNumber": 2, "pageSize": 1000}], "Pagination did not advance deterministically")
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[row] * 1000))) as client:
            try:
                list(DataSF(client).paginate("https://checker.invalid", "SELECT * ORDER BY mapblklot"))
            except ProviderUnavailable:
                pass
            else:
                raise ValueError("Repeated upstream page was accepted")
        # The bounded viewport is a separate production path from batch export.
        rows = [dict(row, mapblklot=f"checker-{i:04d}") for i in range(5)]
        client, adapter = provider(lambda _: httpx.Response(200, json=rows))
        with client:
            features, truncated, _ = adapter.viewport((-122.43, 37.76, -122.40, 37.79), 3)
        require(len(features) == 3 and truncated is True, "Viewport cap/truncation failed")
        return "1,001 records in two stable batch pages; repeat-page rejection; viewport cap 3/5."

    def malformed():
        invalid = [dict(row, mapblklot=42), {k: v for k, v in row.items() if k != "the_geom"},
                   dict(row, the_geom={"type": "Point", "coordinates": [-122.4, 37.7]}),
                   dict(row, the_geom={"type": "Polygon", "coordinates": [[[37, -122], [38, -122], [38, -121], [37, -122]]]})]
        for bad in invalid:
            calls = []
            def reply(_):
                calls.append(1)
                return httpx.Response(200, json=[bad if len(calls) == 1 else row])
            client, adapter = provider(reply)
            with client:
                try:
                    adapter.detail(PARCEL)
                except ProviderUnavailable:
                    pass
                else:
                    raise ValueError("Malformed upstream parcel was accepted")
                good, freshness = adapter.detail(PARCEL)
                require(good.id == PARCEL and freshness.freshness == "live" and len(calls) == 2, "Invalid parcel was cached or poisoned later lookup")
        return "Numeric keys, missing geometry, nonpolygon geometry and swapped coordinates rejected; invalid records not cached."

    with TemporaryDirectory(prefix="parcel-production-probes-") as directory:
        directory = Path(directory)
        # Actual production freeze/setup; expected results never come from it.
        inputs = ReleaseInputs.model_validate({**source, "predictions": {}, "model_version": None})
        frozen = freeze(inputs, load_assumptions(ROOT / "config/economics.live.json", "live"))
        release_path = directory / "synthetic-live-mode.json"
        release_path.write_text(frozen.model_dump_json())

        def timeout_fallback():
            calls = []
            def reply(_):
                calls.append(1)
                if len(calls) > 1:
                    raise httpx.ReadTimeout("controlled timeout")
                return httpx.Response(200, json=[row])
            client, adapter = provider(reply)
            with client, patch("backend.datasf.monotonic", return_value=0):
                first, fresh = adapter.detail(PARCEL)
                _, cached = adapter.detail(PARCEL)
                with patch("backend.datasf.monotonic", return_value=61):
                    last, stale = adapter.detail(PARCEL)
                require(first == last and (fresh.freshness, cached.freshness, stale.freshness) == ("live", "cached", "stale"), "Cache freshness/fallback differs")
                require(len(calls) == 3, "Timeout must attempt initial request plus one retry")
            client, adapter = provider(lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("outage")))
            with client, TestClient(create_app(mode="live", release_path=release_path, parcel_provider=adapter)) as api:
                detail = api.get(f"/api/v1/parcels/{PARCEL}")
                viewport = api.get("/api/v1/parcels", params={"bbox": BBOX})
                require(detail.status_code == viewport.status_code == 200 and detail.json()["source"]["freshness"] == viewport.json()["source"]["freshness"] == "stale", "Release-snapshot fallback lacks stale label")
            return "Real cache live/cached/stale transitions and API detail/viewport snapshot fallbacks verified."

        def unavailable():
            client, adapter = provider(lambda _: httpx.Response(503, json={"error": "outage"}))
            # Missing artifacts are an intentional case; avoid printing their expected traceback.
            with client, patch("backend.api.logging.getLogger"):
                with TestClient(create_app(mode="live", release_path=directory / "missing.json", parcel_provider=adapter)) as api:
                    for path in (f"/api/v1/parcels/{PARCEL}", "/api/v1/parcels?bbox=" + BBOX, "/api/v1/metadata"):
                        response = api.get(path)
                        require(response.status_code == 503 and response.json()["error"]["code"], "Missing provider/cache/artifacts must yield structured 503")
                    response = api.post("/api/v1/scenarios/compare", json={"parcel_id": PARCEL})
                    require(response.status_code == 503 and response.json()["error"]["code"] == "artifacts_unavailable", "Comparison retained absent scoring release")
            return "Actual routes return structured 503 without provider/cache/artifacts."

        def changed_geometry():
            changed = copy.deepcopy(row)
            changed["the_geom"]["coordinates"][0][1][0] += .00001
            for current, parcel_id, reason in ((changed, PARCEL, "parcel_geometry_changed"),
                                                (dict(row, mapblklot="checker-new"), "sf:checker-new", "parcel_not_in_scoring_snapshot")):
                client, adapter = provider(lambda _, current=current: httpx.Response(200, json=[current]))
                with client, TestClient(create_app(mode="live", release_path=release_path, parcel_provider=adapter)) as api:
                    detail = api.get(f"/api/v1/parcels/{parcel_id}")
                    require(detail.status_code == 200 and detail.json()["scoring_available"] is False and detail.json()["scoring_reason"] == reason, "Changed/new parcel incorrectly remains scoreable")
                    response = api.post("/api/v1/scenarios/compare", json={"parcel_id": parcel_id})
                    data = response.json()
                    require(response.status_code == 200 and data["reason"] == reason, "Comparison loses changed/new parcel reason")
                    require(all(a["rank"] is None and a["total"] is None and all(v is None for v in a["normalized"].values()) for a in data["alternatives"]), "Changed/new parcel inherited cached scores")
            return "Changed and new parcels stay visible with reasons and null normalized metrics/totals/ranks through actual API routes."

        def bounded_retry():
            for status, expected_calls in ((429, 2), (503, 2), (400, 1)):
                calls = []
                def reply(_):
                    calls.append(1)
                    return httpx.Response(status, json={"error": "controlled failure"})
                with httpx.Client(transport=httpx.MockTransport(reply)) as client:
                    try:
                        DataSF(client).page("https://checker.invalid", "SELECT *", 1, 1000)
                    except ProviderUnavailable:
                        pass
                    else:
                        raise ValueError("Upstream failure was accepted")
                require(len(calls) == expected_calls, f"HTTP {status} retry count differs: {len(calls)}")
            return "429/503 retry once; nontransient 400 does not retry."

        for id, action in (("pagination", pagination), ("malformed", malformed), ("timeout_fallback", timeout_fallback),
                           ("unavailable", unavailable), ("changed_geometry", changed_geometry), ("bounded_retry", bounded_retry)):
            check(id, action)
    sources = [*sorted((ROOT / "backend").glob("*.py")), *sorted((ROOT / "backend").glob("*.lock"))]
    return {"production_driver": True, "mode": "fault-injection-synthetic", "python_version": platform.python_version(), "checks": checks,
            "production_components": ["backend.datasf.DataSF", "backend.datasf.DataSFParcels", "backend.api.create_app", "backend.release.Release"],
            "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/verification/production-provider-results.json"))
    args = parser.parse_args(argv)
    result = run_probes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if all(c["status"] == "PASS" for c in result["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
