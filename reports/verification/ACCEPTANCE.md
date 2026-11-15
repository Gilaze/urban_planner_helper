# Person 4 acceptance report

Date: November 14, 2026 (America/Los_Angeles). Baseline: `cb642e1` on `main`.
Workstream: `test/verification`.

**Checker implementation is ready for handoff. Product release is blocked.**
The repository still contains only the implementation plan on `origin/main`;
there is no frontend, backend, canonical data release or trained model to accept.
The checks below use explicitly synthetic records. No real-world benefit,
vacancy, model quality, API latency or browser behavior has been established.

## Measured results

| Check | Result | Evidence |
| --- | --- | --- |
| Independent pytest suite | **102 passed, 2 skipped**, 2.41 s | `python -m pytest -c tests/integration/pytest.ini tests/integration -q`; skips identify the absent live API and production provider driver. |
| Scoring hand cases | **39 passed** within the suite | Two-point 2SFCA ratios and exact `2/9` deficit reduction; 400/800 m boundaries; zero populations; monotonic supply; censored travel; percentile ties; missing dimensions; stable ranking ties/Pareto cases; seeded economic ranges. |
| Artifact/model/reference audit | **17 PASS, 0 FAIL, 4 BLOCKED** | [Generated fixture acceptance](fixture/acceptance.md), [machine-readable checks](fixture/results.json), [recalculated metrics](fixture/model_metrics.json). Exit 1 is intentional while product gates are blocked. |
| Browser TypeScript | **Passed** | `npm run typecheck` in `tests/integration/browser/`. |
| Playwright collection | **16 collected** | Eight flows at 1440×900 and 1280×720; collection only, **not executed against a product**. |
| Fault-injection harness | **Passed with test doubles** | Local HTTP tests prove that the harness accepts its controlled double and detects stale-label and changed-geometry regressions. They do not validate a production provider. |

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

These are missing dependencies, not observed defects in nonexistent modules.

| ID / owner | Reproduction | Expected result / release impact |
| --- | --- | --- |
| P4-001 / Backend + Frontend | Run the release CLI without API/browser evidence. | Ready API and runnable UI; integrated flow cannot be signed off until both exist. All candidates affected. |
| P4-002 / Backend + Data + Checker | Inspect `contracts/` and exported artifacts at baseline; neither exists. | Publish OpenAPI/examples and canonical artifacts; agree adapter names, percentile interpolation, target transform and supply-tag conventions in the handoff. Exact wire parity remains blocked. |
| P4-003 / Backend | Run `python -m tests.integration.provider_checks --driver backend.checker_driver:create_driver`. The module does not exist yet. | Wrap the real provider in the documented test driver; all six fault probes must pass through actual provider/cache code. Changed/new parcels must not inherit cached scoring. |
| P4-004 / Data | Run the CLI against an actual release directory; none has been provided. | Audited Oakland/Berkeley/SF exports, frozen predictions, provenance and independent metrics; learned model or documented baseline fallback. Real SF evaluation remains blocked. |
| P4-005 / Frontend + Checker | Run the 16 Playwright tests after publishing the UI and agreeing its accessible hooks. | Both presentation sizes pass with selection, massing, reweight/reset and error states. Current collection cannot establish UI acceptance. |
| P4-006 / Checker + Data | Complete `manual_evidence.example.json` on the presentation machine. | Inspect sources, privacy, zoning/hypothetical/economic language, actual boundary coverage, clipped massing, category lookalikes and SF evaluation evidence. No unobserved item may be marked passed. |

Full setup, exact commands, proposed payload/export mappings, browser hooks and
the provider-driver protocol are documented in
[`tests/integration/README.md`](../../tests/integration/README.md).

Demo fallback: **none exercised**; no demo application exists yet. The checker
supports the plan's fixture-mode fallback but always records that mode and never
converts a synthetic pass into live release acceptance. No product fixes were
made outside this workstream.
