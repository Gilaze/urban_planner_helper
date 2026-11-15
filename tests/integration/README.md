# Person 4: independent verification

Parcel Planner compares five essential-service proposals on one SF physical
parcel. The API owns ranking; Data owns source cleaning and review-activity
predictions; Frontend owns the map and controls. This directory owns independent
checks and release evidence. It does not implement any of those product modules.

The starting commit (`cb642e1`) contains only `IMPLEMENTATION_PLAN.md`. Consequently
the scoring oracle, data audits and test harness can run now; product integration
and presentation-machine acceptance remain blocked until those modules arrive.

## Run from a clean checkout

From the repository root (Python 3.11 is the planned production version):

```sh
python3.11 -m venv tests/integration/.venv
source tests/integration/.venv/bin/activate
python -m pip install -r tests/integration/requirements.lock.txt
python -m pytest -c tests/integration/pytest.ini tests/integration -q
```

Python 3.12 can also run these checks. The initial measured run used 3.12.5;
3.11 was not installed on that machine. Product tests explicitly skip unless
their API URL or provider driver is supplied. A green offline pytest run is not
release sign-off.

Generate the synthetic artifact audit:

```sh
python -m tests.integration.verify_release \
  --mode fixture \
  --artifact-dir tests/integration/fixtures/artifacts \
  --report-dir reports/verification/fixture
```

This intentionally exits **1** while the API, browser, provider and presentation
gates are blocked. It writes `acceptance.md`, machine-readable `results.json`, and
`model_metrics.json`. `release_accepted` is always false in fixture mode. Empty,
skipped or missing evidence cannot pass a gate. The synthetic records include one
hypothetical parcel, all five candidates, a missing economic input, string APNs
and GEOIDs, and small Oakland/Berkeley/SF records. They establish no real coverage,
model quality, vacancy, tax revenue or access benefit.

Regenerate fixtures deterministically after a deliberate schema change:

```sh
python -m tests.integration.fixtures.build_fixture
```

## Integrated release command

```sh
python -m tests.integration.verify_release \
  --mode release \
  --api-base-url http://127.0.0.1:8000/api/v1 \
  --artifact-dir /absolute/path/to/release \
  --bbox=-122.43,37.76,-122.40,37.79 \
  --parcel-id sf:physical-lot-key \
  --oracle-cases /absolute/path/to/backend-loaded-oracle-cases.json \
  --browser-results tests/integration/browser/test-results/results.json \
  --provider-results reports/verification/provider-results.json \
  --manual-evidence /absolute/path/to/completed-review.json
```

Use `--mode fixture` for the same core checks against a fixture-mode integrated
app. `--oracle-cases` must describe records actually loaded into that backend;
the included checker case must never be applied to an unrelated real parcel.
API requests are bounded to 5 seconds and 8 MB by default. The report measures
three cached comparison calls against the plan's subsecond target, not a load
benchmark. Every failure includes an owner, reproduction, expected behavior,
impact and affected categories where applicable. Fixes to product modules go to
their owners.

The optional pytest product entry points use the same checks:

```sh
PARCEL_API_BASE_URL=http://127.0.0.1:8000/api/v1 \
PARCEL_ORACLE_CASES=tests/integration/fixtures/oracle_cases.json \
python -m pytest -c tests/integration/pytest.ini tests/integration/test_live_api.py -m live -q
```

## Hour-3 handoff: provisional payload adapter

**Backend has not published OpenAPI or `contracts/` yet.** The fixture wire names
here are proposed checker conventions. They are not an additional product spec.
At handoff, map the real payload into `api_checks.py` and update the browser
fixtures/hooks with Frontend. Keep expected math independent of backend code.
Do not change production code just to satisfy an arbitrary checker field name.
Record this agreement in `payload_agreement` in the manual evidence.

`fixtures/comparison.json` is the full proposed response. It has `parcel_id`,
`versions` (`schema`, `data`, `model`, `assumptions`), `mode`, `freshness`,
`geometry_hash`, `scoring_geometry_hash`, `scoring_available`, `active_dimensions`,
`effective_weights`, and five `alternatives`. Each alternative has `category_id`,
`raw`, `normalized`, `coverage`, `reason_codes`, `review_activity`, `proposal`,
`total`, `rank` and `pareto`. Unknown values are JSON null. Changed geometry can
still have details, but its response must have a reason and null totals/ranks.

Collections use GeoJSON with top-level `limit`, `truncated`, `source_version` and
`freshness`. Detail fields mirror the canonical parcel fixture and add
`quality_flags` and freshness. Metadata exposes `categories: [{id, label}]`,
`defaults: {weights, priorities}`, `units`, `sources` and `coverage`. Health has
`ready`, `mode` and loaded `versions`. Structured errors use `detail` or `error`.

Oracle conventions to agree before exact production parity:

- Catchment weights are 1 through 400 m, 0.5 through 800 m, then 0. Facility
  denominators use all supported population; the reported deficit decrease is a
  population-weighted mean within 800 m of the proposal. Release targets stay
  frozen. A two-point case checks exact ratios and the deficit decrease `2/9`.
- The percentile oracle uses `100 * (below + equal/2) / n`, with 50 for constant
  references. The plan does not settle interpolation between observations. If
  Backend chooses another midpoint convention, agree it explicitly and update
  the oracle and literal cases together before enforcing exact parity.
- Category normalization uses population standard deviation of training
  `log1p(reviews_count)`, replacing zero standard deviation with one. `target` in
  the checker feature export is the unnormalized log count; the transform is
  recorded separately. Map a normalized production target explicitly if needed.
- Supply includes open and unknown-status records, excluding temporary and
  permanent closures. A peer matches its primary category or deduplicated service
  tags. Other-service counts count remaining locations once. Focal ID is excluded.
  These category/tag conventions also need the Data handoff.
- The economic oracle uses SHA256-separated category/metric random streams and
  linear quantiles. The plan does not specify a PRNG/quantile implementation;
  exact draw parity is not presumed. Tests require repeatability, ordered bounded
  intervals, stable streams and explicit zeros distinct from unknown inputs.

## Data and model export adapter

`artifacts.py:REQUIRED` lists the proposed field names. Geography tables are
`parcels.geoparquet` and `demographics.geoparquet`; other canonical tables are
Parquet. JSON arrays are accepted only in checker fixture mode. GeoParquet WKB
and declared CRS metadata are decoded; WGS84 and EPSG:26910 are checked and area
is independently recomputed in EPSG:26910. APNs/GEOIDs remain strings. Source
APNs cannot map to two physical parcels. Unsupported enrichments remain null.

The manifest records `pipeline_version`, `fixture_mode`, `sources` with HTTPS
attribution, retrieval timestamp, vintage, SHA256 and counts; `artifacts` with
filename/SHA256/row count; explicit `units`; and `coverage` with city/category and
closure counts. See the small complete example in `fixtures/artifacts/`.
`geometry_crs` is needed for JSON fixture geography. The boundary buffer
declaration is checked separately from the required actual spatial review.

Additional audit exports, to coordinate with Data/Backend:

| File | Checker input |
| --- | --- |
| `feature_definitions.json` | Ordered `feature_names` and `distance_crs`; required semantic predictors in `model_audit.py:PREDICTORS`. |
| `model_manifest.json` | Fit IDs, category normalization and training-median slice thresholds for `selection` and `final`; selected approach, supported categories, seed, estimator version and SF evaluation count. A model attempt also records fixed estimator parameters and all four selection trials. |
| `predictions.parquet` | `stage`, `approach`, `place_id`, `category_id`, `city`, `actual_log_count`, `prediction_log_count`; all approaches export the same evaluation IDs, including null predictions for unsupported categories. |
| `scoring_reference.json` | Frozen `data_version`, agreed percentile `method`, `references` arrays and one `rows` entry per SF parcel/category with raw/normalized metrics. This audits percentile ties and the equal mean of economic component percentiles. |

The model audit independently recomputes spatial counts and nearest peers for
both training and parcel features. It recalculates log-count RMSE and tied-rank
Spearman, macro averages on common rows, category baselines and unseen-city
fallbacks. Oakland fits selection transforms; Oakland plus Berkeley fits final
transforms. Berkeley selects; SF is evaluated separately. Review-derived
predictors are rejected. Small slices are marked insufficient, constant-rank
correlation is null, and worst errors and city/category coverage are exported.
The single-SF-evaluation field is an audit declaration; training logs must support
it in the manual review. The checker does not load untrusted estimator pickles.

## Parcel-provider fault injection

Backend supplies a thin driver around its **real** adapter, not a new provider:

```python
def create_driver(*, upstream_url, snapshot_path, page_size, max_records,
                  timeout_s, max_retries):
    # Configure the production provider/parser/cache with these test inputs.
    # Return an object exposing the two methods described below.
    ...
```

`fetch(bbox)` returns normalized GeoJSON with `features`, `truncated`,
`source_version`, `freshness` and `quality_flags`. Unavailability returns
`status_code: 503` or raises an exception with `.status_code == 503`.
`compare(parcel_id)` returns a normalized comparison with `scoring_available`,
`reason` and alternatives. The wrapper translates actual upstream field names
and paging parameters to the controlled fixture protocol: `offset`/`limit`
queries, `{records, next_offset, source_version}` responses, and the records in
`fixtures/provider_records.json`. `snapshot_path` contains verified records and
their version; load it into the actual cache through the wrapper. Keep production
pagination, validation, timeouts, retry and fingerprint logic in the path.

```sh
python -m tests.integration.provider_checks \
  --driver backend.checker_driver:create_driver \
  --output reports/verification/provider-results.json
```

Six probes exercise deterministic pagination/caps, malformed records, timeout to
stale snapshot, 503 with no snapshot, changed geometry losing cached scores, and
one bounded transient retry. No production driver exists at the starting commit;
that absence is explicitly recorded as a blocker, not a passing probe.

## Browser handoff and presentation review

```sh
cd tests/integration/browser
npm ci
npx playwright install chromium
npm run typecheck
npm run test:list
APP_BASE_URL=http://127.0.0.1:5173 \
API_BASE_URL=http://127.0.0.1:8000/api/v1 npm test
```

`test:list` only compiles/collects the 16 checks; it never counts as product
acceptance. The same eight flows run at 1440×900 and 1280×720. The normal flow
uses the actual API. Error tests intercept responses to inject stale data,
unavailable metrics/503, invalid massing, and an older response arriving after a
new selection. WebGL/basemap failure must leave the parcel list usable.

Proposed stable Frontend hooks (adapt to the real accessible UI at handoff):

| Hook | Meaning |
| --- | --- |
| `parcel-selector` | Native select, placeholder with empty value; keyboard operable. |
| `comparison-results` | `data-parcel-id`, `data-status="ready"` after completion. |
| `candidate-{id}` / `alternative-{id}` | Candidate selector and visible result with `data-rank`. |
| `objective-{dimension}` / `priority-{id}` | Native range inputs; defaults one, allow the value two. |
| `massing-preview` | Visible state description with `data-kind="volume"` or `"outline"`, illustrative label. |
| `view-2d` | Toggle with `aria-pressed`; Reset is an accessible button named Reset. |
| `data-freshness` / `unavailable-reason` / `raw-metrics-{id}` | Visible stale label, reasons and raw values/units. |

Copy `manual_evidence.example.json` and fill real observations with screenshots,
source row references, audit paths and machine details. Review reviewer privacy,
attribution, zoning as context rather than approval, explicit vacancy evidence or
hypothetical labeling, scenario-based economics, actual edge coverage, category
lookalikes, clipped massing and the final SF evaluation or its blocker. The
category review should cover 20 records per candidate and 20 ambiguous records,
including clinic and grocery lookalikes. Leave unobserved items `NOT_RUN`; the
report will not accept them. Record any demo fallback and affected categories in
the acceptance report.
