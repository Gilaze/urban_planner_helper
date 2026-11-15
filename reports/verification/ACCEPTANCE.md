# Person 4 acceptance report

Date: November 14, 2026 (America/Los_Angeles). Audited main baseline: `8898718`.
Delivery: direct push to `main`, without PRs.

**Available checks are complete; live release is not accepted.** The independent
checker, Backend and Data unit suites pass on Python 3.11. All six real provider
fault probes pass. Independent Data probes reproduce four product regressions,
tracked with their Data owner in [REGRESSIONS.md](REGRESSIONS.md). Frontend and
full canonical release files are not present in this checkout.

## Measured checks

| Check | Result | Scope |
| --- | --- | --- |
| Combined checker suite | **132 passed, no skips**, 3.33 s | Python 3.11.17, actual fixture HTTP API, real provider probes, hand scoring cases, native artifact/normalization adapters and deliberately corrupted fixtures. |
| Backend's own suite | **10 passed**, 2.263 s | Locked Python 3.11 environment; includes schema release loading and changed-geometry behavior. |
| Data's own suite | **21 passed**, 2.77 s | Its `uv.lock`, Python 3.11.17, refreshed after the latest Census/settings commits. |
| Independent provider fault probes | **6 passed** | Actual parser/cache/pagination/retry/API code; only upstream transport and source records are controlled. Includes new and changed parcels losing scores. |
| Independent Data probes | **4 passed, 4 failed** | Synthetic inputs through actual pipeline functions; no actual SF test labels re-evaluated. |
| Browser TypeScript / collection | **Passed / 16 collected** | Both 1440x900 and 1280x720 configured. **No product browser run**: Frontend is absent. |
| Current acceptance command | **20 PASS, 4 FAIL, 4 BLOCKED, 1 WARN** | [Readable report](current/acceptance.md), [JSON evidence](current/results.json), [qualified Data aggregate review](current/data_report_review.json). Exit 1 is intentional. |

The current fixture HTTP check measured cached calls at approximately 1 ms, below
the plan's one-second target. It is not a live-data or load benchmark. The
synthetic fixture exercises all five categories and deliberately leaves school
FTE unavailable, so economics is omitted from the common ranking axes while
available raw values remain visible. The fixture parcel is hypothetical.

The combined environment is reproducible via
`tests/integration/backend-tests.lock.txt`; the Data environment remains separate
under `pipeline/uv.lock`. [README](../../tests/integration/README.md) contains the
exact setup and verification commands. Raw probe results fingerprint code and
locks, so stale evidence cannot silently pass after source changes.

## Closed blockers and implemented checks

- Planned-interpreter verification now runs on Python 3.11.17.
- No Backend-owned wrapper is required for current provider QA. Production probes
  cover 1,001-row pagination, repeated pages, viewport caps, malformed records,
  cache timeouts, visible API stale freshness, no-provider/cache 503, changed/new
  score invalidation, and bounded transient retries.
- Native Data artifact support now checks actual columns, string APNs/GEOIDs,
  unique grains, fingerprints, joins, closure/category domains, density units and
  MOEs, geographic splits, log targets, feature order and serving fallbacks.
  Large scenario tables stream in batches. Geometry/area and independent neighbor
  checks use up to 25 deterministic sampled rows per table, explicitly reported
  as sampled. Native raw-file behavior is tested with synthetic Parquet fixtures.
- Exported fit IDs and population mean/std can be validated through fingerprinted
  `model_normalization.json`; estimator pickles are not loaded.
- Recent dotenv precedence and Census error-redaction changes pass independent
  synthetic checks, including formatted traceback privacy.

## Release blockers and evidence limits

1. **Data regressions:** SF labels affect training eligibility; selected evaluation
   omits served baseline fallbacks; partial condo assessments appear complete;
   publication preserves checksums from pre-filter files. All four are reproduced
   and owned by Data. Product fixes remain with that owner per the plan.
2. **Raw artifacts:** Data's latest committed reports declare a Census-backed build
   and 4,581 frozen parcel comparisons. The canonical Parquet/frozen prediction
   files are gitignored and absent here. Their real joins, model metrics,
   normalization and actual boundary coverage cannot be signed off from reports
   alone. Supply the full release directory, not just aggregate reports.
3. **Category review:** 160 sampled rows cover 20 records per candidate, 20
   ambiguous records and 20 each of clinic/grocery lookalikes. There are **zero
   completed accept/reject verdicts**. The individual-provider versus facility
   mapping policy needs review before supply estimates are accepted.
4. **Frontend/presentation:** 16 flows are ready for the real UI but unexecuted.
   The presentation-machine review of privacy, attribution, zoning language,
   vacancy/hypothetical labels, economic assumptions, geometry and fallbacks also
   remains unobserved.

Data aggregate arithmetic reconciles for version
`0.1.0+2026-11-14.2+ad464d957df7`. On the reported common SF categories (clinic and
grocery), selected macro RMSE is 1.551121 versus baseline 1.509768. These values
are **reconciled reported aggregates**, not independently recomputed raw metrics.
Earlier SF results were already published; freeze and disclose evaluation history
before describing any subsequent run as an untouched final test. Do not select a
new model using the observed SF outcomes.

## What next

Data fixes the four regressions and provides rebuilt canonical/serving artifacts,
updated fingerprints and normalization evidence. Data and Checker resolve the
category samples. Frontend supplies the real flow in parallel. Checker then runs
the native artifact/frozen-prediction audit, integrated API and both browser sizes,
followed by presentation review. Keep fixture mode explicit until those gates pass.

Current demo fallback exercised: **synthetic API only**. No live release or browser
experience is approved. Historical reports under `fixture/` and `synced-main/`
remain snapshots of earlier milestones; `current/` is the controlling report.
