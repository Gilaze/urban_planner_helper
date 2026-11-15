"""HTTP smoke checks and a provisional payload adapter for the hour-3 handoff.

When Backend publishes contracts/, change payload access here rather than the
independent math. The wire field names below are checker conventions, not an
assertion that a backend already exists.
"""

from __future__ import annotations

import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .artifacts import _xy_points, finite
from .oracle import CANDIDATES, DIMENSIONS, rank, need, sustainability
from .reporting import Report


class Api:
    def __init__(self, base_url, timeout=5):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("API base URL must use HTTP(S), including /api/v1")
        self.timeout = timeout

    def request(self, method, path, payload=None):
        body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        request = Request(self.base_url + path, data=body, method=method,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read(8_000_001)
                content_type = response.headers.get("Content-Type", "")
        except HTTPError as error:
            status, raw = error.code, error.read(8_000_001)
            content_type = error.headers.get("Content-Type", "")
        if len(raw) > 8_000_000:
            raise ValueError("Response exceeds bounded 8 MB smoke-test limit")
        if "json" not in content_type:
            raise ValueError(f"HTTP {status} response is not labeled JSON")
        def reject(value):
            raise ValueError(f"Non-JSON constant {value}")
        return status, json.loads(raw, parse_constant=reject), time.perf_counter() - start


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_comparison(payload, request):
    require(isinstance(payload, dict), "Comparison must be an object")
    require(payload.get("parcel_id") == request["parcel_id"], "Response parcel differs from request")
    versions = payload["versions"]
    require(all(isinstance(versions.get(k), str) and versions[k] for k in ("schema", "data", "model", "assumptions")),
            "Schema, data, model and assumptions versions are required")
    require(payload.get("mode") in {"fixture", "live"}, "Comparison mode must be explicit")
    require(payload.get("freshness") in {"fresh", "stale"}, "Comparison freshness must be explicit")
    alternatives = payload["alternatives"]
    require(isinstance(alternatives, list) and len(alternatives) == 5, "Exactly five actual candidates are required")
    require({a["category_id"] for a in alternatives} == set(CANDIDATES), "Candidate IDs differ from plan")
    require(type(payload.get("scoring_available")) is bool, "scoring_available must be explicit")
    for row in alternatives:
        require(row["rank"] is None or type(row["rank"]) is int, "Rank must be an integer or null")
        require(row["pareto"] is None or type(row["pareto"]) is bool, "Pareto flag must be boolean or null")
        require(set(row["normalized"]) == set(DIMENSIONS), "All normalized dimensions must be explicit")
        require(isinstance(row["raw"], dict) and set(DIMENSIONS) <= row["raw"].keys(), "Raw objectives must be explicit")
        require(isinstance(row["coverage"], dict) and isinstance(row["reason_codes"], list), "Coverage/reason codes required")
        require(isinstance(row["review_activity"]["source"], str) and row["review_activity"]["source"], "Review activity needs its source")
        require(finite(row["review_activity"]["value"], low=0, nullable=True), "Invalid review-activity value")
        for dimension, value in row["normalized"].items():
            require(finite(value, low=0, high=100, nullable=True), f"Invalid normalized {dimension}")
            if value is None:
                require(bool(row["reason_codes"]), "Unavailable metric needs a reason")
        for field in ("retained_tax_usd_per_year", "operating_fte"):
            interval = (row["raw"].get("economic") or {}).get(field)
            if interval is not None:
                require(all(finite(interval.get(k), low=0) for k in ("p10", "p50", "p90")), "Invalid economic interval")
                require(interval["p10"] <= interval["p50"] <= interval["p90"], "Unordered economic interval")
        proposal = row["proposal"]
        require(proposal.get("illustrative") is True and proposal.get("height_m") == 7, "Massing must be illustrative and 7 m")
        if proposal.get("geometry") is not None:
            require(proposal["geometry"].get("type") in {"Polygon", "MultiPolygon"}, "Massing must be polygonal")
            require(all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in _xy_points(proposal["geometry"])), "Massing must be WGS84")
        else:
            require("invalid_geometry" in row["reason_codes"], "Missing massing needs invalid geometry reason")
    if payload["scoring_available"]:
        require(bool(payload.get("geometry_hash")) and payload.get("scoring_geometry_hash") == payload["geometry_hash"],
                "Changed geometry must not inherit snapshot scores")
        expected = rank({a["category_id"]: a["normalized"] for a in alternatives}, request.get("weights"), request.get("priorities"))
        require(set(payload["active_dimensions"]) == set(expected["active_dimensions"]), "Common available dimensions differ")
        for dimension, value in expected["effective_weights"].items():
            require(finite(payload["effective_weights"].get(dimension), low=0) and math.isclose(payload["effective_weights"][dimension], value, abs_tol=1e-8),
                    f"Effective weight differs: {dimension}")
        expected_rows = {row["category_id"]: row for row in expected["alternatives"]}
        for row in alternatives:
            other = expected_rows[row["category_id"]]
            require(row["rank"] == other["rank"] and row["pareto"] is other["pareto"], "Rank or Pareto status differs from independent oracle")
            require(row["total"] is None if other["total"] is None else finite(row["total"]) and math.isclose(row["total"], other["total"], abs_tol=1e-7),
                    "Ranking points differ from independent oracle")
        if expected["reason"]:
            require(bool(payload.get("reason")), "Unranked comparison needs an explanation")
    else:
        require(bool(payload.get("reason")), "Unavailable scoring needs a reason")
        require(all(a["rank"] is None and a["total"] is None for a in alternatives), "Unavailable/changed parcel retains scores")
    return payload


def validate_collection(payload, *, services=False):
    require(payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list), "Expected GeoJSON FeatureCollection")
    require(type(payload.get("truncated")) is bool, "Collection needs truncation metadata")
    require(type(payload.get("limit")) is int and payload["limit"] > 0 and len(payload["features"]) <= payload["limit"], "Collection must have a positive enforced cap")
    require(bool(payload.get("source_version")) and payload.get("freshness") in {"fresh", "stale"}, "Collection needs source version/freshness")
    ids = []
    for feature in payload["features"]:
        require(feature.get("type") == "Feature", "Invalid GeoJSON feature")
        key = feature["properties"]["place_id" if services else "parcel_id"]
        require(isinstance(key, str) and bool(key) and (services or key.startswith("sf:")), "Invalid canonical feature ID")
        ids.append(key)
        geometry = feature["geometry"]
        require(geometry["type"] in ({"Point"} if services else {"Polygon", "MultiPolygon"}), "Unexpected feature geometry")
        require(all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in _xy_points(geometry)), "GeoJSON must use WGS84")
        if services:
            require(feature["properties"]["closure_status"] in {"open", "unknown"}, "Closed facility included in active supply")
    require(len(set(ids)) == len(ids), "Duplicate canonical IDs in viewport")


def run_api_checks(api, report: Report, *, bbox="-122.43,37.76,-122.40,37.79", parcel_id=None, oracle_cases=()):
    try:
        _, health_data, _ = api.request("GET", "/health")
    except Exception:
        health_data = {}
    if not isinstance(health_data, dict):
        health_data = {}
    if isinstance(health_data.get("versions"), dict) and "schema_version" in health_data["versions"]:
        from .published_api import run_checks
        return run_checks(api, report, bbox=bbox, parcel_id=parcel_id, oracle_cases=oracle_cases)
    reproduction = f"python -m tests.integration.verify_release --api-base-url '{api.base_url}'"
    def probe(id, callback, expected):
        try:
            detail = callback()
            report.add(id, "PASS", "Backend", detail or "Expected response received.", reproduction, expected, impact="API contract")
            return True
        except Exception as exc:
            report.add(id, "FAIL", "Backend", exc, reproduction, expected, categories=CANDIDATES)
            return False

    def health():
        status, data, _ = api.request("GET", "/health")
        require(status == 200 and data.get("ready") is True, "API is not ready")
        require(data.get("mode") in {"fixture", "live"} and bool(data.get("versions")), "Health needs mode and loaded versions")
        require(report.mode != "release" or data["mode"] == "live", "Fixture API cannot pass a live release")
        return f"Ready in {data['mode']} mode with versions {data['versions']}."
    if not probe("api.health", health, "Ready API with explicit fixture/live mode and versions."):
        report.add("api.remaining", "BLOCKED", "Backend", "Health failed; remaining API probes were not attempted.", reproduction, "Reachable, ready API.")
        return

    def metadata():
        status, data, _ = api.request("GET", "/metadata")
        require(status == 200, f"Metadata returned {status}")
        require({row["id"] for row in data["categories"]} == set(CANDIDATES), "Metadata candidate IDs differ")
        require(all(row.get("label") for row in data["categories"]), "Category labels required")
        require(data["defaults"]["weights"] == dict.fromkeys(DIMENSIONS, 1), "Default objective weights must be one")
        require(data["defaults"]["priorities"] == dict.fromkeys(CANDIDATES, 1), "Default category priorities must be one")
        require(all(data.get(k) for k in ("units", "sources", "coverage")), "Metadata units/sources/coverage required")
    probe("api.metadata", metadata, "Five candidates, all-one defaults, units, sources and coverage.")

    selected = []
    def parcels():
        status, data, _ = api.request("GET", "/parcels?" + urlencode({"bbox": bbox}))
        require(status == 200, f"Parcels returned {status}")
        validate_collection(data)
        selected.extend(f["properties"]["parcel_id"] for f in data["features"])
        return f"{len(selected)} parcels; truncated={data['truncated']}; freshness={data['freshness']}."
    probe("api.parcels", parcels, "Bounded WGS84 parcel collection with freshness and cap.")
    def services():
        for category in CANDIDATES:
            status, data, _ = api.request("GET", "/services?" + urlencode({"bbox": bbox, "category": category}))
            require(status == 200, f"Services returned {status}")
            validate_collection(data, services=True)
            require(all(category in {f["properties"]["primary_category"], *f["properties"]["service_tags"]} for f in data["features"]),
                    "Service category filter ignored")
    probe("api.services", services, "Active, deduplicated locations filtered by category.")

    def errors():
        requests = [("GET", "/parcels/sf%3A__checker_unknown__", None, 404),
                    ("GET", "/parcels?bbox=garbage", None, 422),
                    ("GET", "/parcels?bbox=10,20,0,5", None, 422),
                    ("GET", "/services?" + urlencode({"bbox": bbox, "category": "not_a_category"}), None, 422)]
        for method, path, body, expected in requests:
            status, data, _ = api.request(method, path, body)
            require(status == expected and isinstance(data, dict) and bool(data.get("detail") or data.get("error")),
                    f"Expected structured {expected} for {path}, received {status}")
    probe("api.errors", errors, "Structured 404/422 errors for unknown IDs, invalid bbox and categories.")
    parcel_id = parcel_id or (selected[0] if selected else None)
    if not parcel_id:
        report.add("api.comparison", "BLOCKED", "Backend", "Viewport has no parcel; supply --parcel-id or a populated --bbox.", reproduction,
                   "At least one testable parcel.")
        return
    detail_data = {}
    def detail():
        status, data, _ = api.request("GET", "/parcels/" + quote(parcel_id, safe=""))
        require(status == 200 and data["parcel_id"] == parcel_id, "Parcel detail lookup failed")
        require(data.get("source_version") and data.get("freshness") in {"fresh", "stale"}, "Detail provenance missing")
        require(isinstance(data["quality_flags"], list) and data.get("geometry_hash"), "Detail quality flags/fingerprint required")
        require(data.get("vacancy_evidence") if data["vacancy_status"] == "source_indicated_vacant" else data.get("hypothetical") is True,
                "Parcel needs explicit vacancy evidence or hypothetical label")
        detail_data.update(data)
    probe("api.parcel_detail", detail, "Namespaced detail with vacancy/hypothetical evidence and geometry fingerprint.")
    def comparisons():
        default = {"parcel_id": parcel_id, "weights": dict.fromkeys(DIMENSIONS, 1), "priorities": dict.fromkeys(CANDIDATES, 1)}
        changed = {"parcel_id": parcel_id, "weights": {"need": 2, "economic": 1, "sustainability": 0.5}, "priorities": {"clinic": 2}}
        responses, latencies = [], []
        for body in (default, default, changed, default):
            status, data, elapsed = api.request("POST", "/scenarios/compare", body)
            require(status == 200, f"Comparison returned {status}")
            validate_comparison(data, body)
            if detail_data:
                require(data["geometry_hash"] == detail_data["geometry_hash"], "Detail and scoring geometries differ")
                from pyproj import Transformer
                from shapely.geometry import shape
                from shapely.ops import transform
                project = Transformer.from_crs(4326, 26910, always_xy=True).transform
                parcel_shape = transform(project, shape(detail_data["geometry"]))
                if parcel_shape.is_valid and not parcel_shape.is_empty:
                    for alternative in data["alternatives"]:
                        geometry = alternative["proposal"]["geometry"]
                        if geometry is not None:
                            footprint = transform(project, shape(geometry))
                            require(footprint.is_valid and parcel_shape.buffer(0.01).covers(footprint), "Illustrative footprint is invalid or outside parcel")
                            require(.35 <= footprint.area / parcel_shape.area <= .65, "Illustrative footprint should occupy approximately 50% of parcel")
            responses.append(data)
            latencies.append(elapsed)
        stable = lambda data: {k: data[k] for k in ("versions", "alternatives", "active_dimensions", "effective_weights", "scoring_available")}
        require(stable(responses[0]) == stable(responses[1]) == stable(responses[3]), "Repeated calls/reset are not deterministic")
        require(max(latencies[1:]) < 1, f"Cached comparison >=1s: {latencies[1:]}")
        return f"Default/repeat/reweight/reset checked; cached timings {[round(t * 1000, 2) for t in latencies[1:]]} ms."
    probe("api.comparison", comparisons, "Stable independent ranking, economic intervals, reset and subsecond cached responses.")
    def invalid_weights():
        bodies = [dict(parcel_id=parcel_id, weights=dict.fromkeys(DIMENSIONS, 0)),
                  dict(parcel_id=parcel_id, weights={"need": -1, "economic": 1, "sustainability": 1}),
                  dict(parcel_id=parcel_id, priorities={"clinic": 3}), dict(parcel_id=parcel_id, priorities={"bogus": 1})]
        for body in bodies:
            status, data, _ = api.request("POST", "/scenarios/compare", body)
            require(status == 422 and isinstance(data, dict) and bool(data.get("detail") or data.get("error")), "Invalid weights/priorities need structured 422")
    probe("api.invalid_weights", invalid_weights, "Reject all-zero/negative weights and out-of-range/unknown priorities.")
    for case in oracle_cases:
        def exact(case=case):
            status, data, _ = api.request("POST", "/scenarios/compare", {"parcel_id": case["parcel_id"]})
            require(status == 200, "Oracle parcel comparison failed")
            row = next(r for r in data["alternatives"] if r["category_id"] == case["category_id"])
            expected_need = need(case["populations"], case["existing_distances_m"], case["proposed_distances_m"], target=case.get("target"))
            expected_travel = sustainability(case["populations"], case["baseline_distances_m"], case["proposed_distances_m"], censored=case.get("censored"))
            for metric, values, keys in (("need", expected_need, ("deficit_before", "deficit_after", "benefit", "covered_population")),
                                          ("sustainability", expected_travel, ("before_m", "after_m", "saved_m", "baseline_coverage"))):
                for key in keys:
                    actual, expected = row["raw"][metric][key], values[key]
                    require(actual is None if expected is None else finite(actual) and math.isclose(actual, expected, abs_tol=1e-8), f"Oracle mismatch {metric}.{key}")
        probe(f"api.oracle.{case['parcel_id']}.{case['category_id']}", exact, "Hand-calculated raw access/travel values from a backend-loaded matching fixture.")
