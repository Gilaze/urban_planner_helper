# Person 4: independent verification

The Backend schema 1.0 contract and Data pipeline are on `main`. This directory
owns independent verification and release evidence. Expected calculations never
import production scoring functions. Regression fixes and independent proofs are
tracked in the verification reports. Delivery is a direct push to `main`, without
PRs, per the user's instruction.

Start with `reports/verification/ACCEPTANCE.md` and
`reports/verification/REGRESSIONS.md`. The four Data code regressions remain fixed
in pipeline 0.1.1 and all eight independent probes pass. The latest audit uses
committed repository inputs only, per the user's final scope: Person 4 owns tests
and verification reports; product/data changes stay with their teammates.

All 160 committed category samples now have a Checker review in
`reports/verification/category-review.csv`: 80 label-consistent, 45 rejected, and
35 requiring owner evidence. The 16 browser flows were executed against the
existing `simulator/`; all fail before the planner interaction is available.
See `reports/verification/BROWSER_QA.md` and `CATEGORY_REVIEW.md`. Full canonical
release files are absent from tracked repository inputs.

## Clean-checkout setup

Keep the Backend and Data environments separate so their locks remain intact:

```sh
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r tests/integration/backend-tests.lock.txt
uv sync --project pipeline --locked
cd tests/integration/browser
npm ci
npm run typecheck
npm run test:list
```

`uv` can provision Python 3.11 if it is not already installed. The latest runs
used 3.11.17: the combined checker/Backend environment, Backend's 10 own tests,
and Data's 36 own tests all execute on the planned production interpreter. The
lighter `tests/integration/requirements.lock.txt` remains available for checker
oracles and JSON/Parquet fixtures without Backend libraries.

Start the fixture API in another terminal from the repository root:

```sh
PARCEL_PLANNER_MODE=fixture backend/.venv/bin/python -m uvicorn backend.api:app \
  --host 127.0.0.1 --port 8000
```

Then run the checker against it:

```sh
PARCEL_API_BASE_URL=http://127.0.0.1:8000/api/v1 \
PARCEL_SOURCE_FIXTURE=contracts/fixture-inputs.json \
backend/.venv/bin/python -m pytest -c tests/integration/pytest.ini tests/integration -q
backend/.venv/bin/python -m unittest discover -s backend/tests -v
pipeline/.venv/bin/python -m pytest pipeline/tests -q
```

Without an API URL, its HTTP check explicitly skips. Without Backend libraries,
the production-provider check explicitly skips. Never interpret those skips as
release acceptance. The two controlled-server harness tests require localhost
binding; production provider probes themselves use an in-process mock transport.

## Actual provider and Data probes

```sh
backend/.venv/bin/python -m tests.integration.production_provider_checks \
  --output reports/verification/production-provider-results.json
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 \
  pipeline/.venv/bin/python -m tests.integration.pipeline_probes \
  --output reports/verification/pipeline-probe-results.json
```

The provider command passes all six probes: deterministic pagination/caps,
malformed records, timeout fallback/stale labels, no-provider/no-cache 503,
changed/new parcels losing scores, and bounded retries. `httpx.MockTransport`
replaces only the upstream network. Real parsers, cache, pagination, retries,
release matching and FastAPI routes stay in the path. The live-mode setup release
is synthetic, not real SF data.

The Data command now exits **0**: all eight checks pass in pipeline 0.1.1. Inputs are
small synthetic records; the actual SF test labels are never revisited. It checks
dotenv precedence, Census error privacy, independent feature distances and train normalization, then tests SF label
isolation, consistency between evaluated and served baseline fallbacks, unknown
condo assessments, and published-file checksums. Results include owner,
reproduction, expected behavior and impact. Code/config/lock fingerprints bind
the results to the tested sources; changing those sources requires rerunning the
probes. Failures are imported into the acceptance report rather than hidden by
a green unit suite. Older 0.1.0 reports remain historical until a full
build/train/publish with 0.1.1; producer-version checks prevent treating them as
fixed release evidence.

The older `provider_checks.py --driver module:factory` protocol remains for future
providers. It is not needed to verify the current DataSF implementation.

## Reproduce current acceptance

With the API running:

```sh
backend/.venv/bin/python -m tests.integration.verify_release \
  --mode fixture --artifact-format pipeline \
  --artifact-dir data/artifacts/release \
  --api-base-url http://127.0.0.1:8000/api/v1 \
  --source-fixture contracts/fixture-inputs.json \
  --provider-results reports/verification/production-provider-results.json \
  --pipeline-probe-results reports/verification/pipeline-probe-results.json \
  --data-reports-dir reports/data \
  --category-review reports/verification/category-review.csv \
  --browser-results reports/verification/current/browser-results.json \
  --manual-evidence reports/verification/current/manual-evidence.json \
  --report-dir reports/verification/current
```

The current report records 24 passes, three failures, two blockers and one
warning. Exit 1 is intentional. It writes readable `acceptance.md`, JSON checks,
and a qualified Data aggregate review. `release_accepted` is always false in
fixture mode. Category mappings, browser flows and presentation readiness fail;
unrebuilt reports and missing canonical artifacts remain blocked. The combined
checker suite has 142 passing tests; Data has 36 and Backend has 10.

`--category-review` accepts a Checker-owned CSV without changing Data's source
sample. Every original sample key/field and its SHA256 must match; each row needs
an `accept`, `reject` or `needs_review` verdict and a note. Rejected or unresolved
mappings do not pass release review. These are listing-label judgments from the
committed sample, not verified facility counts or an estimated error rate.

For a real release, use `--mode release`, its live API URL, its full canonical
artifact directory, and matching oracle cases. Also pass actual Playwright JSON
via `--browser-results` and completed observations via `--manual-evidence`.
`--source-fixture` is only for a matching, coverage-supported synthetic source
fixture; use `--oracle-cases` for other deliberately backend-loaded test records.
Never apply the bundled checker parcel to an unrelated real parcel.

The API client caps responses at 8 MB and uses a five-second timeout by default.
The latency check measures cached comparisons on this machine, not a load test.

## Actual wire and artifact formats

The API authority is `contracts/openapi.json`. Schema 1.0 requests use
`objective_weights` and `category_priorities`; comparisons have explicit version
fields, `parcel_source`, per-alternative `need`/`economic`/`sustainability`, and a
shared `massing`. `published_api.py` maps those fields to independent checks.
Native source-fixture checks independently project coordinates into EPSG:26910,
then verify raw access/travel for all five candidates over HTTP.

Use `--artifact-format pipeline` for Data's **full** `data/artifacts/release/`,
rather than only the smaller `artifacts/person2/` serving projection. `auto`
detects the native `source_manifest.outputs` format. `pipeline_artifacts.py`
accepts the actual fields (`category`, `rep_lon`, `site_label`,
`same_category_count_*`, `target_log1p_reviews_count`, and
`place_eval_predictions.parquet`). It checks every key/domain/fingerprint, APN
physical-lot mapping, Places/Reviews row preservation, split/target joins, and
five-candidate coverage. Large parcel/category tables stream in batches.

Geometry/area and independent spatial recalculation use up to 25 reproducibly
sampled rows per table, seed 42. This limit is explicitly reported. Those checks
exclude focal IDs, closed supply and transit from the other-service complement,
and use native service tags. Geography uses GeoParquet WKB/CRS metadata, WGS84
exports and EPSG:26910 distances. Population and covariate geographies remain
separate. A complete file audit cannot establish unsupplied raw source coverage.

Native frozen prediction checks recalculate baselines, common evaluation rows,
log-count RMSE and tied-rank Spearman. Aggregate `reports/data` figures are
reconciled separately and **never** called independent raw-prediction validation.
Release-specific normalization still needs a fingerprinted
`model_normalization.json` export with `selection` and `final` objects:

```json
{"selection":{"fit_place_ids":[],"categories":{}},
 "final":{"fit_place_ids":[],"categories":{}}}
```

Use actual fit IDs and `{category: {"mean": ..., "std": ...}}`; an unfitted stage
is explicitly empty. The checker recalculates permitted fit rows and population
standard deviation (zero becomes one). Include its SHA256 under the native
manifest's `outputs`. Estimator pickles are not loaded.

The older JSON arrays under `fixtures/artifacts/` and `fixtures/comparison.json`
are internal checker test formats, not instructions for other owners to rename
their exports. `--artifact-format checker` selects that legacy format. Regenerate
it with `python -m tests.integration.fixtures.build_fixture`. These synthetic
records establish no real vacancy, demand, tax revenue or access improvement.

The independent percentile convention is `100 * (below + equal/2) / n`, with 50
for a constant reference; it matches the published Backend fixture. Economic
oracles use stable category/metric streams and linear quantiles, while exact
production PRNG parity is not presumed. Tests check repeatability, bounded ordered
ranges, zeros versus unknowns, common dimensions, priorities and Pareto cases.

## Browser and presentation handoff

Once the planner flow is integrated with the API, run the same eight flows at
1440x900 and 1280x720:

```sh
cd tests/integration/browser
npx playwright install chromium
APP_BASE_URL=http://127.0.0.1:5173 \
API_BASE_URL=http://127.0.0.1:8000/api/v1 npm test
```

`test:list` only compiles/collects 16 checks; it is not a browser execution. Normal
selection/preview/reweight/reset uses the actual API. Error tests inject stale
source labels, unavailable metrics/503, invalid massing, and an older comparison
arriving after a new selection. WebGL/basemap failure must preserve the parcel
list and results. Browser fixtures use the published Backend payloads.

Proposed hooks, to agree with Frontend's actual accessible UI:

| Hook | Meaning |
| --- | --- |
| `parcel-selector` | Native keyboard-operable select, empty placeholder value. |
| `comparison-results` | `data-parcel-id`, `data-status="ready"` on completion. |
| `candidate-{id}` / `alternative-{id}` | Selector and visible result with `data-rank`. |
| `objective-{dimension}` / `priority-{id}` | Native ranges; defaults one, allow two. |
| `massing-preview` | Visible description, `data-kind="volume"` or `"outline"`, illustrative label. |
| `view-2d` | Toggle with `aria-pressed`; accessible Reset button. |
| `data-freshness` / `unavailable-reason` / `raw-metrics-{id}` | Visible stale label, reasons and raw values/units. |

Complete `manual_evidence.example.json` with observed machine details,
screenshots and source references. Review privacy, attribution, zoning as context,
vacancy/hypothetical labeling, scenario economics, actual boundary coverage,
category lookalikes, clipped massing and the SF evaluation history. Leave
unobserved items `NOT_RUN`. Category verdicts in Data's CSV use `accept`, `reject`
or `needs_review`; unresolved/rejected mappings do not pass. Do not tune against
previously inspected SF test labels and present that as an untouched final test.

The current observations are in `reports/verification/current/manual-evidence.json`.
Unobservable UI/real-data checks remain `NOT_RUN`; recording an observation file
does not make the presentation gate pass. No acquired external inputs are used
by the current repository-only audit, and no real 0.1.1 release is claimed.
