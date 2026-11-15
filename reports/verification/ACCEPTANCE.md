# Person 4 acceptance report

> Latest update: Person 2's new 0.1.1 Drive handoff passes the quick integrity check and is ready for functional testing. See [PERSON2_HANDOFF.md](PERSON2_HANDOFF.md). The full audit below predates main `4cebb7f`; its old artifact/category/browser statuses are a historical snapshot, not a verdict on the new handoff.

Date: November 14, 2026 (America/Los_Angeles). Audited main: `35f3947`.
Final scope: **Person 4 only, using committed repository inputs**. Delivery is a
direct push to main without a PR. Changes are confined to `tests/integration/`
and `reports/verification/`; no teammate's product code or Data reports changed.

**Verification is complete for the available fixture/code/sample inputs; release
is not accepted.** The current command reports **24 PASS, 3 FAIL, 2 BLOCKED,
1 WARN**. Browser/presentation readiness and category mappings fail; full real
artifacts and refreshed release reports remain unavailable under this scope.

## Measured checks

| Check | Result | Evidence scope |
| --- | --- | --- |
| Combined checker suite | **142 passed, no skips**, 3.44 s | Python 3.11.17, actual fixture HTTP API, independent scoring, real provider code with controlled transport, artifact adapters and exact-source-bound category reviews. |
| Data suite | **36 passed**, 3.30 s | Unchanged pipeline 0.1.1, locked Python 3.11 environment. |
| Backend suite | **10 passed**, 0.184 s | Release/schema/cache/ranking and geometry behavior. |
| Independent Data probes | **8 PASS, 0 FAIL** | Repository synthetic inputs; all four previously fixed Data regressions remain closed. |
| Provider fault probes | **6 PASS** | Pagination/caps, malformed records, retries, stale fallback, unavailable sources and changed/new score invalidation. |
| Category sample review | **160 reviewed: 80 accept, 45 reject, 35 need owner evidence** | Committed names/categories/tags only; no external facility verification or population error-rate estimate. |
| Browser build / TypeScript | **Passed** | Existing simulator build and checker TypeScript compile; build alone does not prove UI integration. |
| Actual browser flow | **0 passed, 16 failed, 0 skipped** | Eight planned flows at 1440×900 and 1280×720, installed Chrome. Planner entry point absent; downstream assertions not reached. |
| Current acceptance command | **24 PASS, 3 FAIL, 2 BLOCKED, 1 WARN** | [Readable details](current/acceptance.md), [checks](current/results.json), [aggregate/sample review](current/data_report_review.json). Exit 1 is intentional. |

## Findings and teammate handoff

**Frontend / Person 1:** the current `simulator/` is a city viewer. With only
tracked inputs, it remains at a missing-city-data error. The planned parcel
selector, comparison panel, weights and proposal preview are not integrated.
Both viewport screenshots show the same loader error, without horizontal overflow
or a framework overlay. The 16 scenarios fail at entry rather than demonstrate
16 distinct defects. See [BROWSER_QA.md](BROWSER_QA.md) for console/HTTP evidence,
interaction limits and reproduction. The fixture API and simulator are running
locally on ports 8765 and 5173 for inspection.

**Data / Person 2:** resolve the category findings in
[CATEGORY_REVIEW.md](CATEGORY_REVIEW.md) and [the 160 annotated rows](category-review.csv).
All 20 clinic lookalikes and 20 grocery lookalikes retain candidate supply tags
despite an `other` primary group. Other notes identify credential/school-scope
conflicts and uncertain individual-provider versus facility capacity. The review
is stored separately; Data's original CSV and taxonomy are unchanged. The
verifier rejects stale source hashes, changed fields and incomplete review rows.

**Data / Person 2:** supply the full 0.1.1 canonical and serving handoff, including
frozen predictions and normalization fit IDs/means/std. Those outputs are
ignored and are not supplied by tracked repository files. Existing 0.1.0 reports
remain historical. Their arithmetic reconciles, but it cannot validate the fixed
release's actual joins, assessment totals, normalization or boundary coverage.
Earlier SF results have already been inspected; preserve that evaluation history.
No actual SF re-evaluation was performed in this audit.

**Team + Checker:** complete the actual presentation review after the integrated
flow and permitted artifacts exist. [Recorded observations](current/manual-evidence.json)
leave unavailable UI, real boundary and frozen-SF checks `NOT_RUN`; supplying an
observation file does not make the presentation gate pass. The only exercised
complete API fallback remains the explicitly synthetic fixture.

## Scope and input policy

The user narrowed the task to Person 4 and repository-provided data. Preparatory
Data edits were removed. Inputs acquired before that restriction and their
untrained build were excluded from active pipeline paths and from all current
verification evidence. No downloaded source rows, credentials, model binaries or
real-data outputs are committed, and no rebuilt real 0.1.1 release is claimed.

The previous four Data fixes remain on main. New product findings are recorded
with their owners in [REGRESSIONS.md](REGRESSIONS.md). Exact checker setup and
acceptance commands are in [the README](../../tests/integration/README.md).
