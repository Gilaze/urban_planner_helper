# Parcel Planner

The FastAPI backend runs a synthetic San Francisco scenario with five service alternatives. The fixture uses hypothetical geometry, population, service locations, review activity and economic assumptions.

## Run the fixture

From the repository root with Python 3.11 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements.lock
.\.venv\Scripts\python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Open [API docs](http://127.0.0.1:8000/docs) or load the [fixture viewport](http://127.0.0.1:8000/api/v1/parcels?bbox=-122.43,37.76,-122.4,37.79). FastAPI serves the UI at the same origin when a frontend build exists at `frontend/dist` before startup. The development API accepts requests from Vite on localhost port 5173.

The [OpenAPI schema](contracts/openapi.json) and [response examples](contracts/examples/) include the default comparison, parcel geometry, service overlays, metadata and readiness. After a contract change, regenerate them with `python -m backend.export_contract`.

## Run a live release

Install `backend/requirements-release.lock`, then prepare the [canonical backend inputs](contracts/README.md). Freeze a new release whenever input data, predictions or assumptions change. The API loads the release once at startup.

```powershell
python -m pip install -r backend/requirements-release.lock
python -m backend.build_release --input artifacts/person2 --output artifacts/release.json
$env:PARCEL_PLANNER_MODE = "live"
$env:PARCEL_PLANNER_RELEASE = "artifacts/release.json"
python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Set `DATASF_APP_TOKEN` in the server environment to query the [DataSF land-use dataset](https://data.sf.gov/d/c5ge-t6pj). `PARCEL_PLANNER_PARCEL_URL` and `PARCEL_PLANNER_PARCEL_SOURCE_VERSION` override the URL and source label. Set `PARCEL_PLANNER_CORS_ORIGINS` to a comma-separated list to override development origins.

Viewport queries return at most 500 parcels and report possible truncation. Retrieve a whole bounded area with the batch exporter, which uses stable ordering and successive 1,000-row pages. The exporter replaces the output after all pages validate.

```powershell
python -m backend.snapshot --bbox=-122.53,37.70,-122.35,37.84 --output artifacts/datasf-parcels.geojson
```

The land-use source has parcel, multi-parcel and analytical geographies. The provider filters to physical lots by `geography_type` and maps `mapblklot` to `sf:<lot>`. Its rows have no explicit vacancy field. Live responses label vacancy as unverified and sites as hypothetical. Use the canonical snapshot to supply verified eligibility and APNs.

Pass `--assessment-year YEAR` to the release builder to join the [secured roll](https://data.sf.gov/d/wv5m-vpq2) for eligible parcels using verified, uniquely assigned APNs. This option also requires `DATASF_APP_TOKEN`. Set `PARCEL_PLANNER_ASSESSOR_URL` to override the roll endpoint. The response exposes assessed land and improvements with the roll year. It keeps missing values null and sums values only across completely matched, unambiguous APNs. Category taxable sales, the retention rate and operating FTE require separate inputs. The included live assumptions leave them unknown. Supply a versioned live assumptions file with `--assumptions PATH`.

With a provider outage, the API uses a validated cached response or the release's parcel snapshot and marks the result stale. Parcel details show whether cached scoring applies. A new, ineligible or changed parcel remains visible with scores unavailable. A missing scoring release returns 503 for comparisons, metadata and service overlays. A missing provider and snapshot returns 503 for parcels.

## Check the backend

```powershell
python -m unittest discover -s backend/tests -v
```

These checks require the release dependencies. DataSF requests in the tests use a mocked transport.
