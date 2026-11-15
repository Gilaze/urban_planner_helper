"""Exercise the fault-injection harness, not a product adapter or release gate."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .provider_checks import PROBES, run_probes


class HarnessDriver:
    """Minimal test double that speaks only the checker's controlled protocol."""

    def __init__(self, *, upstream_url, snapshot_path, page_size, max_records, timeout_s, max_retries):
        self.url, self.page_size, self.cap = upstream_url, page_size, max_records
        self.timeout, self.retries = timeout_s, max_retries
        self.snapshot = json.loads(snapshot_path.read_text()) if snapshot_path else None
        self.latest = []

    def fetch(self, bbox):
        offset, rows, quality = 0, [], []
        freshness, source_version = "fresh", None
        while True:
            try:
                for attempt in range(self.retries + 1):
                    try:
                        with urlopen(f"{self.url}?offset={offset}&limit={self.page_size}", timeout=self.timeout) as response:
                            data = json.load(response)
                        break
                    except (HTTPError, URLError, TimeoutError):
                        if attempt == self.retries:
                            raise
            except (HTTPError, URLError, TimeoutError):
                if self.snapshot is None:
                    return {"status_code": 503}
                data = self.snapshot
                rows = data["records"]
                freshness, source_version = "stale", data["source_version"]
                break
            source_version = data["source_version"]
            for row in data["records"]:
                if not isinstance(row["parcel_id"], str) or row["geometry"]["type"] != "Polygon":
                    quality.append("malformed_upstream_record")
                else:
                    rows.append(row)
            offset = data.get("next_offset")
            if offset is None or len(rows) > self.cap:
                break
        self.latest = rows[:self.cap]
        return {"features": [{"type": "Feature", "geometry": r["geometry"], "properties": r} for r in self.latest],
                "truncated": len(rows) > self.cap or offset is not None, "freshness": freshness,
                "quality_flags": quality, "source_version": source_version}

    def compare(self, parcel_id):
        old = next(r for r in self.snapshot["records"] if r["parcel_id"] == parcel_id)
        new = next(r for r in self.latest if r["parcel_id"] == parcel_id)
        changed = old["geometry_hash"] != new["geometry_hash"]
        return {"scoring_available": not changed, "reason": "parcel_geometry_changed" if changed else None,
                "alternatives": [{"total": None, "rank": None}] if changed else [{"total": 10, "rank": 1}]}


def test_fault_harness_accepts_its_conforming_double():
    results = run_probes(HarnessDriver)
    assert {r["id"] for r in results} == set(PROBES)
    assert all(r["status"] == "PASS" for r in results), results


def test_fault_harness_exposes_stale_and_cached_score_regressions():
    class Broken(HarnessDriver):
        def fetch(self, bbox):
            data = super().fetch(bbox)
            data["freshness"] = "fresh"
            return data

        def compare(self, parcel_id):
            return {"scoring_available": True, "alternatives": [{"rank": 1, "total": 50}]}
    statuses = {r["id"]: r["status"] for r in run_probes(Broken)}
    assert statuses["timeout_fallback"] == statuses["changed_geometry"] == "FAIL"
    assert statuses["pagination"] == "PASS"
