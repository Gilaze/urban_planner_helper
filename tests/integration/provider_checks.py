"""Fault-injection probes for the real parcel adapter through a small driver.

No replacement provider is shipped here. Backend supplies a wrapper exposing
fetch(bbox) and compare(parcel_id) around its production adapter. See README.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import inspect
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent / "fixtures"
BBOX = [-122.43, 37.76, -122.40, 37.79]
PROBES = ("pagination", "malformed", "timeout_fallback", "unavailable", "changed_geometry", "bounded_retry")


def run_probes(factory):
    records = json.loads((FIXTURES / "provider_records.json").read_text())
    state = {"scenario": "pagination", "requests": [], "records": records}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [2])[0])
            state["requests"].append((offset, limit))
            scenario = state["scenario"]
            if scenario == "timeout_fallback":
                time.sleep(0.15)
            if scenario in {"unavailable", "bounded_retry", "timeout_fallback"}:
                self.send_response(503)
                body = {"error": "controlled outage"}
            else:
                self.send_response(200)
                data = copy.deepcopy(state["records"])
                if scenario == "malformed":
                    data.insert(1, {"parcel_id": 42, "geometry": {"type": "Point", "coordinates": [999, 999]}})
                if scenario == "changed_geometry":
                    data[0]["geometry"]["coordinates"][0][1][0] += 0.00001
                    data[0]["geometry_hash"] = "changed-checker-geometry"
                body = {"records": data[offset:offset + limit],
                        "next_offset": offset + limit if offset + limit < len(data) else None,
                        "source_version": "checker-upstream-v1"}
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(body).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        with TemporaryDirectory(prefix="parcel-checker-") as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(json.dumps({"records": records, "source_version": "checker-snapshot-v1"}))
            for scenario in PROBES:
                state.update(scenario=scenario, requests=[])
                try:
                    driver = factory(upstream_url=f"http://127.0.0.1:{server.server_port}/parcels",
                                     snapshot_path=None if scenario in {"unavailable", "bounded_retry"} else snapshot,
                                     page_size=2, max_records=3, timeout_s=0.05, max_retries=1)
                    response = None
                    try:
                        response = driver.fetch(BBOX)
                    except Exception as exc:
                        if scenario not in {"unavailable", "bounded_retry"} or getattr(exc, "status_code", None) != 503:
                            raise
                    if scenario in {"unavailable", "bounded_retry"}:
                        assert response is None or response.get("status_code") == 503, "Provider and snapshot absent must yield structured 503"
                    else:
                        assert response is not None and isinstance(response.get("features"), list), "Expected normalized FeatureCollection"
                        if scenario == "pagination":
                            ids = [f["properties"]["parcel_id"] for f in response["features"]]
                            assert len(ids) == len(set(ids)) == 3 and response.get("truncated") is True, "Cap/pagination/dedup failed"
                            assert state["requests"][:2] == [(0, 2), (2, 2)], "Pagination must advance in deterministic bounded pages"
                        elif scenario == "malformed":
                            assert all(isinstance(f["properties"]["parcel_id"], str) and f["geometry"]["type"] in {"Polygon", "MultiPolygon"}
                                       for f in response["features"]), "Malformed upstream record leaked through"
                            assert "malformed_upstream_record" in response.get("quality_flags", []), "Malformed row must be reported"
                        elif scenario == "timeout_fallback":
                            assert response.get("freshness") == "stale" and response.get("source_version") == "checker-snapshot-v1", "Timeout must return verified stale snapshot"
                        elif scenario == "changed_geometry":
                            result = driver.compare(records[0]["parcel_id"])
                            assert result.get("scoring_available") is False and result.get("reason") == "parcel_geometry_changed", "Changed geometry inherited snapshot scoring"
                            assert all(a.get("total") is None and a.get("rank") is None for a in result.get("alternatives", [])), "Changed parcel retains cached ranks"
                    if scenario in {"timeout_fallback", "unavailable", "bounded_retry"}:
                        assert 1 <= len(state["requests"]) <= 2, "Transient retries must be bounded to initial + one retry"
                    if scenario == "bounded_retry":
                        assert len(state["requests"]) == 2, "Expected the configured single retry on 503"
                    results.append({"id": scenario, "status": "PASS", "detail": f"{len(state['requests'])} upstream requests"})
                except Exception as exc:
                    results.append({"id": scenario, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", required=True, help="Production wrapper module:function; see README")
    parser.add_argument("--output", type=Path, default=Path("reports/verification/provider-results.json"))
    args = parser.parse_args(argv)
    module_name, name = args.driver.rsplit(":", 1)
    factory = getattr(importlib.import_module(module_name), name)
    checks = run_probes(factory)
    data = {"driver": args.driver, "driver_source": inspect.getsourcefile(factory),
            "production_driver": not module_name.startswith("tests."), "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))
    return 0 if data["production_driver"] and all(c["status"] == "PASS" for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
