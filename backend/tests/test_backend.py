import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import geopandas as gpd
import httpx
import pandas as pd
from fastapi.testclient import TestClient
from shapely import affinity
from shapely.geometry import Point as ShapelyPoint, box, mapping, shape

from backend.api import create_app
from backend.assessment import enrich_assessments
from backend.build_release import build_inputs
from backend.datasf import PAGE_SIZE, PARCEL_URL, DataSF, DataSFParcels, ProviderUnavailable
from backend.economics import load_assumptions, summarize_economics
from backend.geometry import unproject
from backend.ranking import percentile
from backend.release import ROOT, ReleaseInputs, freeze
from backend.schemas import Point, ServiceFeature, ServiceProperties
from backend.spatial import METERS_PER_MILE, SpatialScoring

BASE = "/api/v1"
PARCEL_ID = "sf:fixture-001"
BBOX = {"bbox": "-122.43,37.76,-122.4,37.79"}


def inputs_fixture():
    return ReleaseInputs.model_validate_json((ROOT / "contracts/fixture-inputs.json").read_bytes())


def coordinates(x, y=0):
    point = unproject(ShapelyPoint(550000 + x, 4180000 + y))
    return point.x, point.y


class ScoringTests(unittest.TestCase):
    def test_two_step_catchment_and_vehicle_miles(self):
        service = ServiceFeature(
            id="existing",
            properties=ServiceProperties(place_id="existing", categories=["clinic"], status="active"),
            geometry=Point(coordinates=coordinates(0)),
        )
        wide_coverage = unproject(box(545000, 4175000, 555000, 4185000))
        local = [(coordinates(0), 100), (coordinates(600), 200)]
        need, sustainability = SpatialScoring(local, [service], wide_coverage).score(coordinates(600))["clinic"]
        self.assertAlmostEqual(need.target_accessibility, 0.00375)
        self.assertAlmostEqual(need.deficit_before, 2 / 9)
        self.assertAlmostEqual(need.deficit_after, 0)
        self.assertAlmostEqual(need.deficit_reduction, 2 / 9)
        self.assertEqual(need.covered_population, 300)
        self.assertAlmostEqual(sustainability.distance_before_m, 400)
        self.assertEqual(sustainability.distance_after_m, 0)
        self.assertEqual(sustainability.baseline_coverage_pct, 100)
        self.assertAlmostEqual(sustainability.avoided_vehicle_miles_per_day.base, 12000 / METERS_PER_MILE)

        narrow = unproject(box(549900, 4179900, 550700, 4180100))
        need, sustainability = SpatialScoring(local, [service], narrow).score(coordinates(600))["clinic"]
        self.assertEqual(need.reason, "incomplete_catchment_coverage")
        self.assertEqual(sustainability.censored_baseline_population, 200)
        self.assertAlmostEqual(sustainability.baseline_coverage_pct, 100 / 3)

    def test_percentile_ties_and_economic_seed(self):
        self.assertEqual(percentile(3, [3, 3]), 50)
        self.assertEqual(percentile(3, [1, 3, 3, 3, 4]), 50)
        self.assertEqual(percentile(0, [1, 3, 3]), 0)
        assumptions = load_assumptions(ROOT / "config/economics.fixture.json", "fixture")
        first = summarize_economics(assumptions)
        self.assertEqual(first, summarize_economics(assumptions))
        self.assertEqual(first["school"].retained_sales_tax_usd_per_year.p50, 0)
        self.assertIsNone(first["school"].operating_fte)


class ProviderTests(unittest.TestCase):
    def test_pagination_advances_past_one_thousand(self):
        pages = []

        def reply(request):
            page = json.loads(request.content)["page"]
            pages.append(page)
            number = page["pageNumber"]
            rows = [{"id": number * PAGE_SIZE + index} for index in range(PAGE_SIZE if number == 1 else 1)]
            return httpx.Response(200, json={"data": rows})

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            rows = list(DataSF(client).paginate(PARCEL_URL, "SELECT id ORDER BY id"))
        self.assertEqual(len(rows), 1001)
        self.assertEqual(pages, [
            {"pageNumber": 1, "pageSize": 1000}, {"pageNumber": 2, "pageSize": 1000}
        ])

    def test_timeout_retry_and_stale_cache(self):
        calls = []
        parcel = inputs_fixture().parcels[0]

        def reply(request):
            calls.append(request)
            if len(calls) > 1:
                raise httpx.ReadTimeout("test timeout")
            return httpx.Response(200, json=[{
                "mapblklot": "fixture-001", "geography_type": "parcel",
                "the_geom": parcel.geometry.model_dump(mode="json"),
            }])

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            provider = DataSFParcels(DataSF(client))
            with patch("backend.datasf.monotonic", return_value=0):
                self.assertEqual(provider.detail(PARCEL_ID)[1].freshness, "live")
                self.assertEqual(provider.detail(PARCEL_ID)[1].freshness, "cached")
            with patch("backend.datasf.monotonic", return_value=65):
                self.assertEqual(provider.detail(PARCEL_ID)[1].freshness, "stale")
        self.assertEqual(len(calls), 3)

    def test_malformed_parcel_is_not_cached(self):
        with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=[{"mapblklot": "fixture-001", "geography_type": "parcel"}])
        )) as client:
            with self.assertRaises(ProviderUnavailable):
                DataSFParcels(DataSF(client)).detail(PARCEL_ID)

    def test_assessment_join_retains_zero_and_preserves_unknown_fields(self):
        parcel = inputs_fixture().parcels[0]
        parcel.properties.source_apns = ["0010001", "0010002"]

        def reply(request):
            query = json.loads(request.content)["query"]
            self.assertIn("'0010001', '0010002'", query)
            self.assertIn("closed_roll_year = 2024", query)
            return httpx.Response(200, json=[
                {
                    "parcel_number": apn,
                    "closed_roll_year": "2024",
                    "assessed_land_value": "0",
                    "assessed_improvement_value": improvements,
                }
                for apn, improvements in [("0010001", "42"), ("0010002", None)]
            ])

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            parcels, joins = enrich_assessments([parcel], {parcel.id}, 2024, DataSF(client))
        self.assertEqual(joins, 1)
        self.assertEqual(parcels[0].properties.assessed_land_usd, 0)
        self.assertIsNone(parcels[0].properties.assessed_improvement_usd)
        self.assertEqual(parcels[0].properties.assessment_year, 2024)


class APITests(unittest.TestCase):
    def test_fixture_reweighting_and_errors(self):
        with TestClient(create_app(mode="fixture")) as client:
            def compare(**request_fields):
                return client.post(f"{BASE}/scenarios/compare", json={"parcel_id": PARCEL_ID, **request_fields})

            self.assertEqual(client.get(f"{BASE}/health").json()["mode"], "fixture")
            parcel = client.get(f"{BASE}/parcels/{PARCEL_ID}").json()
            default = compare().json()
            self.assertEqual(default["active_dimensions"], ["need", "sustainability"])
            self.assertEqual(default["alternatives"][0]["category_id"], "clinic")
            self.assertTrue(shape(parcel["geometry"]).covers(shape(default["massing"]["geometry"])))
            prioritized = compare(
                objective_weights={"need": 1, "economic": 0, "sustainability": 0},
                category_priorities={"school": 2},
            ).json()
            self.assertEqual(prioritized["alternatives"][0]["category_id"], "school")
            self.assertGreater(prioritized["alternatives"][0]["total"], 100)
            self.assertFalse(prioritized["alternatives"][0]["pareto"])
            self.assertEqual(compare().json(), default)
            unavailable = compare(objective_weights={"need": 0, "economic": 1, "sustainability": 0}).json()
            self.assertEqual(unavailable["reason"], "no_available_positively_weighted_objectives")
            self.assertTrue(all(row["rank"] is None for row in unavailable["alternatives"]))
            self.assertEqual(compare(objective_weights=dict.fromkeys(["need", "economic", "sustainability"], 0)).status_code, 422)
            self.assertEqual(compare(objective_weights={"need": 1e308, "sustainability": 1e308}).json()["effective_weights"]["need"], 0.5)
            self.assertEqual(compare(parcel_id="sf:missing").status_code, 404)
            self.assertEqual(client.get(f"{BASE}/parcels", params={"bbox": "nan,0,1,1"}).status_code, 422)
            self.assertEqual(len(client.get(f"{BASE}/services", params=BBOX | {"category": "clinic"}).json()["features"]), 1)

    def test_parquet_release_is_deterministic_and_rejects_changed_geometry(self):
        fixture = inputs_fixture()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            parcels = gpd.GeoDataFrame(
                [{"parcel_id": parcel.id, "source_apns": ["0010001"], "vacancy_status": "source_indicated_vacant",
                  "geometry": shape(parcel.geometry.model_dump())} for parcel in fixture.parcels],
                crs=4326,
            )
            parcels["representative_point"] = gpd.GeoSeries([
                shape(parcel.properties.representative_point.model_dump()) for parcel in fixture.parcels
            ], crs=4326).to_crs(26910)
            parcels.to_crs(26910).to_parquet(directory / "parcels.geoparquet")
            gpd.GeoDataFrame(
                [{"geoid": row.geoid, "population": row.population,
                  "geometry": ShapelyPoint(row.coordinates)} for row in fixture.populations],
                crs=4326,
            ).to_crs(26910).to_parquet(directory / "demographics.geoparquet")
            pd.DataFrame([
                {"place_id": row.id, "longitude": row.geometry.coordinates[0],
                 "latitude": row.geometry.coordinates[1], "closure_status": "active",
                 "primary_category": row.properties.categories[0], "service_tags": []}
                for row in fixture.services
            ]).to_parquet(directory / "places.parquet")
            manifest = {
                "data_version": "test-only", "parcel_source_version": "test-only-parcels",
                "parcel_retrieved_at": "2026-01-01T00:00:00Z", "sources": {}, "coverage": {},
                "hypothetical_parcel_id": PARCEL_ID,
                "spatial_coverage": fixture.spatial_coverage.model_dump(mode="json"),
            }
            (directory / "backend_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            inputs = build_inputs(directory)
            assumptions = load_assumptions(ROOT / "config/economics.live.json", "live")
            serialized = freeze(inputs, assumptions).model_dump_json()
            self.assertEqual(serialized, freeze(build_inputs(directory), assumptions).model_dump_json())
            path = directory / "release.json"
            path.write_text(serialized, encoding="utf-8")

            geometry = shape(fixture.parcels[0].geometry.model_dump())
            serving = {"geometry": geometry, "down": False}

            def reply(_):
                if serving["down"]:
                    return httpx.Response(502)
                return httpx.Response(200, json=[{
                    "mapblklot": "fixture-001", "geography_type": "parcel",
                    "the_geom": mapping(serving["geometry"]),
                }])

            with httpx.Client(transport=httpx.MockTransport(reply)) as http:
                provider = DataSFParcels(DataSF(http))
                app = create_app(mode="live", release_path=path, parcel_provider=provider)
                with TestClient(app) as client:
                    self.assertEqual(client.get(f"{BASE}/health").status_code, 200)
                    with patch("backend.datasf.monotonic", return_value=0):
                        detail = client.get(f"{BASE}/parcels/{PARCEL_ID}").json()
                        self.assertTrue(detail["scoring_available"])
                        self.assertEqual(detail["properties"]["source_apns"], ["0010001"])
                        listed = client.get(f"{BASE}/parcels", params=BBOX).json()["features"][0]
                        self.assertEqual(listed["properties"]["vacancy_status"], "source_indicated_vacant")
                        self.assertNotIn("vacancy_unverified", listed["properties"]["quality_flags"])
                    serving["geometry"] = affinity.translate(geometry, xoff=0.0001)
                    with patch("backend.datasf.monotonic", return_value=65):
                        listed = client.get(f"{BASE}/parcels", params=BBOX).json()["features"][0]
                        self.assertEqual(listed["properties"]["vacancy_status"], "unknown")
                        compared = client.post(f"{BASE}/scenarios/compare", json={"parcel_id": PARCEL_ID}).json()
                        self.assertEqual(compared["reason"], "parcel_geometry_changed")
                    serving["down"] = True
                    with patch("backend.datasf.monotonic", return_value=130):
                        compared = client.post(f"{BASE}/scenarios/compare", json={"parcel_id": PARCEL_ID}).json()
                        self.assertEqual(compared["parcel_source"]["freshness"], "stale")
                        self.assertEqual(compared["reason"], "parcel_geometry_changed")

                cold_provider = DataSFParcels(DataSF(http))
                with TestClient(create_app(mode="live", release_path=path, parcel_provider=cold_provider)) as client:
                    parcel = client.get(f"{BASE}/parcels/{PARCEL_ID}").json()
                    self.assertEqual(parcel["source"]["freshness"], "stale")
                    self.assertTrue(parcel["scoring_available"])
                    self.assertEqual(parcel["properties"]["source_apns"], ["0010001"])

    def test_live_missing_release_returns_503(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"DATASF_APP_TOKEN": ""}):
            with self.assertLogs("backend.api", level="ERROR"):
                with TestClient(create_app(mode="live", release_path=Path(directory) / "missing.json")) as client:
                    self.assertEqual(client.get(f"{BASE}/health").status_code, 503)
                    self.assertEqual(client.get(f"{BASE}/parcels", params=BBOX).status_code, 503)
                    compared = client.post(f"{BASE}/scenarios/compare", json={"parcel_id": PARCEL_ID})
                    self.assertEqual(compared.json()["error"]["code"], "artifacts_unavailable")

    def test_built_frontend_and_api_share_origin(self):
        with TemporaryDirectory() as directory:
            frontend = Path(directory)
            (frontend / "index.html").write_text("<h1>Frontend build</h1>", encoding="utf-8")
            with TestClient(create_app(frontend_dir=frontend, mode="fixture")) as client:
                self.assertIn("Frontend build", client.get("/").text)
                self.assertEqual(client.get(f"{BASE}/health").status_code, 200)
                self.assertEqual(client.get(f"{BASE}/unknown").json()["error"]["code"], "route_not_found")


if __name__ == "__main__":
    unittest.main()
