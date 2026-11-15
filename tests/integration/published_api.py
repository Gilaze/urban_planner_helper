"""Adapter for the schema 1.0 contract published by Backend on main.

Only field mapping lives here. Expected scoring remains in the independent
oracle; no production calculations are imported.
"""

import math
from urllib.parse import quote, urlencode

from .artifacts import _xy_points, finite, read_json
from .oracle import CANDIDATES, DIMENSIONS, need, rank, sustainability


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_comparison(data, request):
    require(data["parcel_id"] == request["parcel_id"], "Response parcel differs from request")
    require(all(data.get(k) for k in ("schema_version", "data_version", "assumption_version")) and "model_version" in data,
            "Schema/data/model/assumption versions must be explicit")
    require(data["mode"] in {"fixture", "live"}, "Comparison mode is missing")
    require(data["parcel_source"]["freshness"] in {"fixture", "live", "cached", "stale"}, "Parcel freshness is missing")
    rows = data["alternatives"]
    require(len(rows) == 5 and {r["category_id"] for r in rows} == set(CANDIDATES), "Expected five actual candidates")
    normalized = {r["category_id"]: {d: r["normalized"][d] for d in DIMENSIONS} for r in rows}
    weights = dict.fromkeys(DIMENSIONS, 1)
    weights.update(request.get("objective_weights", {}))
    expected = rank(normalized, weights, request.get("category_priorities"))
    require(data["active_dimensions"] == expected["active_dimensions"], "Common active dimensions differ")
    require(set(data["unavailable_dimensions"]) == set(DIMENSIONS) - set(data["active_dimensions"]), "Unavailable dimensions differ")
    for dimension, value in expected["effective_weights"].items():
        require(finite(data["effective_weights"][dimension]) and math.isclose(data["effective_weights"][dimension], value, abs_tol=1e-8), "Effective weights differ")
    expected_rows = {r["category_id"]: r for r in expected["alternatives"]}
    for row in rows:
        wanted = expected_rows[row["category_id"]]
        require(type(row["rank"]) is int if wanted["rank"] is not None else row["rank"] is None, "Rank type differs")
        require(row["rank"] == wanted["rank"] and row["pareto"] is wanted["pareto"], "Rank/Pareto differs from independent oracle")
        require(row["total"] is None if wanted["total"] is None else finite(row["total"]) and math.isclose(row["total"], wanted["total"], abs_tol=1e-7), "Ranking points differ")
        review = row["review_activity"]
        require(finite(review["predicted_log1p_reviews_count"], low=0, nullable=True), "Invalid review-activity value")
        require(review["source"] in {"fixture", "model", "category_baseline", "unavailable"}, "Review-activity source is missing")
        if review["predicted_log1p_reviews_count"] is None:
            require(review["source"] == "unavailable" and bool(review["reason"]), "Unavailable review activity needs a reason")
        access, travel, economic = row["need"], row["sustainability"], row["economic"]
        if access["deficit_reduction"] is not None:
            require(math.isclose(access["deficit_reduction"], access["deficit_before"] - access["deficit_after"], abs_tol=1e-9)
                    and access["deficit_reduction"] >= -1e-9, "Accessibility deficit increased or reduction differs")
        if travel["distance_before_m"] is not None:
            require(travel["distance_after_m"] <= travel["distance_before_m"] + 1e-8, "Nearest-service distance worsened")
        if travel["local_population"]:
            require(math.isclose(travel["baseline_coverage_pct"], 100 * travel["usable_baseline_population"] / travel["local_population"], abs_tol=1e-8), "Baseline coverage denominator differs")
        for field in ("retained_sales_tax_usd_per_year", "operating_fte"):
            interval = economic[field]
            if interval is not None:
                require(all(finite(interval[k], low=0) for k in ("p10", "p50", "p90")) and interval["p10"] <= interval["p50"] <= interval["p90"], "Invalid economic interval")
        tax, jobs = row["normalized"]["retained_sales_tax"], row["normalized"]["operating_fte"]
        econ_expected = (tax + jobs) / 2 if tax is not None and jobs is not None else None
        require(row["normalized"]["economic"] == econ_expected, "Economic percentile components differ")
    if expected["reason"]:
        require(bool(data["reason"]), "Unranked alternatives need an explanation")
    massing = data["massing"]
    require(massing["height_m"] == 7 and massing["illustrative"] is True, "Massing must be illustrative at 7 m")
    if massing["geometry"] is None:
        require(bool(massing["reason"]), "Missing massing requires a reason")
    else:
        require(all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in _xy_points(massing["geometry"])), "Massing must be WGS84")
    return data


def fixture_cases(path):
    """Build distance matrices from source records, independent of backend spatial code."""
    from pyproj import Transformer
    project = Transformer.from_crs(4326, 26910, always_xy=True).transform
    source = read_json(path)
    populations = source["populations"]
    points = [project(*row["coordinates"]) for row in populations]
    result = []
    for parcel in source["parcels"]:
        proposal = project(*parcel["properties"]["representative_point"]["coordinates"])
        for category in CANDIDATES:
            services = [project(*s["geometry"]["coordinates"]) for s in source["services"]
                        if category in s["properties"]["categories"] and s["properties"]["status"] in {"active", "unknown"}]
            distances = [[math.dist(p, service) for service in services] for p in points]
            result.append({"parcel_id": parcel["id"], "category_id": category,
                           "populations": [p["population"] for p in populations], "existing_distances_m": distances,
                           "proposed_distances_m": [math.dist(p, proposal) for p in points],
                           "baseline_distances_m": [min(row) if row else None for row in distances]})
    return result


def run_checks(api, report, *, bbox, parcel_id=None, oracle_cases=()):
    reproduction = f"python -m tests.integration.verify_release --api-base-url '{api.base_url}'"
    def probe(id, action, expected):
        try:
            detail = action()
            report.add(id, "PASS", "Backend", detail or "Published schema 1.0 check passed.", reproduction, expected, impact="API contract")
        except Exception as exc:
            report.add(id, "FAIL", "Backend", exc, reproduction, expected, categories=CANDIDATES)

    def get(path):
        status, data, _ = api.request("GET", path)
        require(status == 200, f"GET {path} returned {status}")
        return data

    def health():
        data = get("/health")
        require(data["ready"] is True and data["versions"]["schema_version"] == "1.0", "API is not ready with schema 1.0")
        require(report.mode != "release" or data["mode"] == "live", "Fixture API cannot sign off a live release")
        return f"Ready {data['mode']} API; versions {data['versions']}."
    probe("api.health", health, "Ready API with explicit mode and loaded versions.")
    def metadata():
        data = get("/metadata")
        require({c["id"] for c in data["categories"]} == set(CANDIDATES), "Candidate metadata differs")
        require(data["objective_weights"] == dict.fromkeys(DIMENSIONS, 1) and data["category_priorities"] == dict.fromkeys(CANDIDATES, 1), "Default controls must all be one")
        require(all(data.get(k) for k in ("units", "sources", "coverage")), "Metadata attribution/units/coverage missing")
    probe("api.metadata", metadata, "Five categories, defaults, units, sources and coverage.")
    selected = []
    def collection(data, *, service=False):
        require(data["type"] == "FeatureCollection" and 0 < data["limit"] and len(data["features"]) <= data["limit"] and type(data["truncated"]) is bool, "Invalid bounded GeoJSON collection")
        ids = [f["id"] for f in data["features"]]
        require(len(set(ids)) == len(ids), "Duplicate IDs in viewport")
        for feature in data["features"]:
            require(all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in _xy_points(feature["geometry"])), "Collection is not WGS84")
            require(feature["geometry"]["type"] in ({"Point"} if service else {"Polygon", "MultiPolygon"}), "Wrong collection geometry type")
    def parcels():
        data = get("/parcels?" + urlencode({"bbox": bbox}))
        collection(data)
        require(data["source"]["version"] and data["source"]["freshness"] in {"fixture", "live", "cached", "stale"}, "Parcel provenance missing")
        selected.extend(f["id"] for f in data["features"])
    probe("api.parcels", parcels, "Capped, deduplicated parcels with freshness and source version.")
    def services():
        for category in CANDIDATES:
            data = get("/services?" + urlencode({"bbox": bbox, "category": category}))
            collection(data, service=True)
            require(data["data_version"] and all(category in f["properties"]["categories"] and f["properties"]["status"] in {"active", "unknown"} for f in data["features"]), "Service category/status filter differs")
    probe("api.services", services, "Active deduplicated supply with category filtering.")
    parcel_id = parcel_id or (selected[0] if selected else None)
    if not parcel_id:
        report.add("api.comparison", "BLOCKED", "Backend", "No selectable parcel; supply --parcel-id or a populated --bbox.", reproduction, "At least one testable parcel.")
        return
    detail = {}
    def parcel_detail():
        detail.update(get("/parcels/" + quote(parcel_id, safe="")))
        props = detail["properties"]
        require(props["parcel_id"] == parcel_id and isinstance(props["quality_flags"], list), "Parcel detail ID/quality flags missing")
        require(props["vacancy_status"] == "source_indicated_vacant" or props["hypothetical"] is True, "Parcel lacks vacancy or hypothetical labeling")
    probe("api.parcel_detail", parcel_detail, "Canonical parcel detail with vacancy/hypothetical labeling.")
    def compare(body):
        status, data, elapsed = api.request("POST", "/scenarios/compare", body)
        require(status == 200, f"Comparison returned {status}")
        validate_comparison(data, body)
        return data, elapsed
    def comparisons():
        default = {"parcel_id": parcel_id}
        bodies = [default, default, {**default, "objective_weights": {"need": 1, "economic": 0, "sustainability": 0}, "category_priorities": {"school": 2}},
                  {**default, "objective_weights": {"need": 0, "economic": 1, "sustainability": 0}}, default]
        responses = [compare(body) for body in bodies]
        require(responses[0][0] == responses[1][0] == responses[-1][0], "Repeated comparison/reset differs")
        require(max(t for _, t in responses[1:]) < 1, "Cached comparison exceeds one second")
        if detail and responses[0][0]["massing"]["geometry"] is not None:
            from shapely.geometry import shape
            from shapely.ops import transform
            from pyproj import Transformer
            project = Transformer.from_crs(4326, 26910, always_xy=True).transform
            parcel = transform(project, shape(detail["geometry"]))
            footprint = transform(project, shape(responses[0][0]["massing"]["geometry"]))
            require(footprint.is_valid and parcel.buffer(.01).covers(footprint) and .35 <= footprint.area / parcel.area <= .65, "Massing is invalid, outside parcel or not approximately 50%")
        return f"Default/repeat/reweight/unavailable-weight/reset pass; cached timings {[round(t*1000, 2) for _, t in responses[1:]]} ms."
    probe("api.comparison", comparisons, "Independent ranking/Pareto, unavailable axes, clipped massing and deterministic cached comparisons.")
    def errors():
        requests = [("GET", "/parcels/sf%3A__checker_unknown__", None, 404), ("GET", "/parcels?bbox=bad", None, 422),
                    ("GET", "/parcels?bbox=10,20,0,5", None, 422),
                    ("POST", "/scenarios/compare", {"parcel_id": parcel_id, "objective_weights": dict.fromkeys(DIMENSIONS, 0)}, 422),
                    ("POST", "/scenarios/compare", {"parcel_id": parcel_id, "category_priorities": {"school": 3}}, 422)]
        for method, path, body, expected in requests:
            status, data, _ = api.request(method, path, body)
            require(status == expected and data["error"]["code"] and data["error"]["message"], f"Expected structured {expected} for {path}")
    probe("api.errors", errors, "Structured unknown-parcel and invalid bbox/weight/priority errors.")
    for case in oracle_cases:
        def exact(case=case):
            data, _ = compare({"parcel_id": case["parcel_id"]})
            row = next(r for r in data["alternatives"] if r["category_id"] == case["category_id"])
            access = need(case["populations"], case["existing_distances_m"], case["proposed_distances_m"], target=case.get("target"))
            travel = sustainability(case["populations"], case["baseline_distances_m"], case["proposed_distances_m"], censored=case.get("censored"))
            values = [(row["need"][k], access[v]) for k, v in (("deficit_before", "deficit_before"), ("deficit_after", "deficit_after"), ("deficit_reduction", "benefit"), ("covered_population", "covered_population"))]
            values += [(row["sustainability"]["distance_before_m"], travel["before_m"]), (row["sustainability"]["distance_after_m"], travel["after_m"])]
            for actual, expected in values:
                require(actual is None if expected is None else finite(actual) and math.isclose(actual, expected, abs_tol=1e-7), "Raw access/travel differs from source-fixture oracle")
        probe(f"api.oracle.{case['parcel_id']}.{case['category_id']}", exact, "Raw need/travel agrees with independently projected fixture source records.")
