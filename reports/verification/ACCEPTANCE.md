# Person 4 acceptance report

Date: November 14, 2026 (America/Los_Angeles). Initial baseline: `cb642e1`.
Synchronized Backend baseline: `2e4724d`. Delivery: direct push to `main`.

**Checker and fixture API verification pass. Live product release is blocked.**
The synchronized main includes the Backend and published schema 1.0 contract.
The checker now uses the actual request/response names and independently verifies
the Backend's raw fixture metrics for all five candidates. Frontend, a canonical
live data release and a trained model are still unavailable. No real-world
benefit, vacancy, model quality or product browser behavior is established.

## Measured results

| Check | Result | Evidence |
| --- | --- | --- |
| Independent pytest suite with fixture HTTP API | **112 passed, 1 skipped** | API URL/source fixture enabled as shown below; the production-provider driver is not configured. |
| Backend's own unit suite | **10 passed** | `backend/.venv/bin/python -m unittest discover -s backend/tests -v`. |
| Scoring hand cases | **40 passed** within the suite | Two-point 2SFCA ratios and exact `2/9` deficit reduction; 400/800 m boundaries; zero populations; monotonic supply; censored travel; percentile ties; missing dimensions; stable ranking ties/Pareto cases; seeded economic ranges; extreme finite weights. |
| Artifact/model/reference and API audit | **29 PASS, 0 FAIL, 3 BLOCKED** | [Synchronized fixture acceptance](synced-main/acceptance.md), [machine-readable checks](synced-main/results.json), [recalculated metrics](synced-main/model_metrics.json). Exit 1 is intentional while browser, independent provider and presentation evidence are blocked. |
| Browser TypeScript | **Passed** | `npm run typecheck` in `tests/integration/browser/`. |
| Playwright collection | **16 collected** | Eight flows at 1440×900 and 1280×720; collection only, **not executed against a product**. |
| Fault-injection harness | **Passed with test doubles** | Local HTTP tests prove that the harness accepts its controlled double and detects stale-label and changed-geometry regressions. They do not validate a production provider. |

Combined checker command, with the fixture Backend running on port 8765:

```sh
PARCEL_API_BASE_URL=http://127.0.0.1:8765/api/v1 \
PARCEL_SOURCE_FIXTURE=contracts/fixture-inputs.json \
python -m pytest -c tests/integration/pytest.ini tests/integration -q
```

The HTTP audit measured cached fixture comparisons at 0.78–0.90 ms on this
machine. This is a local synthetic measurement, not a live-data load benchmark.
The Backend source fixture and checker artifact fixture are separate synthetic
datasets. Their passing checks do not validate a shared live artifact release.

Measured environment: macOS arm64, Python 3.12.5, Node 24.15.0. Python 3.11 was
not installed; run the pinned suite on the planned production interpreter before
presentation sign-off. Python dependencies, including transitive packages, are
locked under `tests/integration/`; npm dependencies and their lock are scoped to
its browser directory. The two provider-harness tests require localhost binding.

## Implemented coverage

- Independent need/access, sustainability, ranking, Pareto, empirical percentile
  and Monte Carlo reference calculations; no imports from production scorers.
- Column/type/key, APN/GEOID, join grain, category/tag, privacy, closure, source
  vintage, manifest fingerprint, CRS/geometry, area, null and coverage audits.
  Deliberately corrupted fixtures verify failure detection beyond checksums.
- Independent training and parcel neighbor counts in EPSG:26910 with focal ID
  and closure exclusion; geographic splits, leakage checks, train-only category
  normalization/baselines/slice thresholds, common evaluation rows, support
  minimums, model gate, tied-rank Spearman and log-count RMSE recalculation.
- Worst errors and category/city coverage are exported separately for Berkeley
  and SF. Fixture slices have fewer than 30 rows and are **insufficient**. The
  fixture uses a category baseline; it is not an evaluated learned model.
- API smoke probes for readiness/metadata, capped collections, services, parcel
  details, structured errors, hand-loaded oracle cases, default/repeat/reweight/
  reset, versioning, independent ranking, massing bounds and cached latency.
- Production-provider probe protocol for pagination, malformed records, timeout
  fallback, no-cache 503, changed geometry and bounded retries.
- Browser flows for selection/preview/reset, keyboard selection, stale labels,
  unavailable API, delayed responses, missing basemap/WebGL, invalid geometry
  and unavailable metrics; manual evidence template for visual/source review.

All five candidates are covered by synthetic fixtures: `clinic`, `pharmacy`,
`grocery`, `bank_credit_union`, `school`. The school fixture lacks operating FTE,
so its economic objective is unavailable; economic ranking is removed from the
common comparison while available raw values remain. The sole parcel has unknown
vacancy and is explicitly hypothetical. A leading-zero APN and a 12-digit string
GEOID are preserved. This is a verification fixture, not a demo site.

## Outstanding integration blockers

These are missing dependencies or evidence, not observed product defects.

| ID / owner | Reproduction | Expected result / release impact |
| --- | --- | --- |
| P4-001 / Frontend | Run browser tests; there is no frontend app yet. | Runnable UI; the API fixture passes but the full presentation flow is blocked. All candidates affected. |
| P4-002 / Data + Checker | Inspect real canonical data/model exports; none are supplied. | Backend OpenAPI/examples now exist and API/percentile parity passes. Final canonical field mappings, target transform and supply-tag conventions still need the Data handoff. |
| P4-003 / Backend | Run `python -m tests.integration.provider_checks --driver backend.checker_driver:create_driver`. The module does not exist yet. | Wrap the real provider in the documented test driver; all six fault probes must pass through actual provider/cache code. Changed/new parcels must not inherit cached scoring. |
| P4-004 / Data | Run the CLI against an actual release directory; none has been provided. | Audited Oakland/Berkeley/SF exports, frozen predictions, provenance and independent metrics; learned model or documented baseline fallback. Real SF evaluation remains blocked. |
| P4-005 / Frontend + Checker | Run the 16 Playwright tests after publishing the UI and agreeing its accessible hooks. | Both presentation sizes pass with selection, massing, reweight/reset and error states. Current collection cannot establish UI acceptance. |
| P4-006 / Checker + Data | Complete `manual_evidence.example.json` on the presentation machine. | Inspect sources, privacy, zoning/hypothetical/economic language, actual boundary coverage, clipped massing, category lookalikes and SF evaluation evidence. No unobserved item may be marked passed. |

Full setup, exact commands, proposed payload/export mappings, browser hooks and
the provider-driver protocol are documented in
[`tests/integration/README.md`](../../tests/integration/README.md).

Demo fallback: **fixture API exercised; no demo UI exercised**. The checker
always records fixture mode and never converts a synthetic pass into live
release acceptance. No production fixes were made outside this workstream.
