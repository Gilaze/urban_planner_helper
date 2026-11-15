# Person 4 acceptance report

Date: November 14, 2026 (America/Los_Angeles). Synchronized baseline: `d4347bc`.
Fixed pipeline: **0.1.1**. Delivery: direct push to `main`, without PRs.

**All four Data code regressions are fixed and their independent proofs pass.**
Live release remains blocked by unrebuilt/unavailable real artifacts and UI/review
evidence. No actual SF test was rerun and no historical metrics were fabricated.

## Measured checks

| Check | Result | Scope |
| --- | --- | --- |
| Combined checker suite | **135 passed, no skips**, 3.10 s | Python 3.11.17, fixture HTTP API, real provider code, independent scoring, native artifact/normalization adapters, fallback availability and producer-version checks. |
| Data suite | **36 passed**, 3.53 s | Locked Python 3.11 environment, including new held-out-label, fallback-policy, assessment and publication regressions. |
| Backend suite | **10 passed** | Release/schema/cache/ranking checks remain green. |
| Independent Data probes | **8 PASS, 0 FAIL** | The same synthetic adversarial probes that previously exposed the four bugs. |
| Provider fault probes | **6 PASS** | Actual parsers, cache, pagination/retry and API routes with controlled upstream transport. |
| Current acceptance command | **24 PASS, 0 FAIL, 5 BLOCKED, 1 WARN** | [Readable details](current/acceptance.md), [JSON checks](current/results.json), [qualified aggregate review](current/data_report_review.json). Exit 1 means missing release evidence, not a remaining reproduced code failure. |

The fixes make eligibility training-only, apply the same selected prediction policy
in evaluation and serving, propagate unknown condo assessment components to null,
and fingerprint each published projection while preserving canonical provenance.
See [REGRESSIONS.md](REGRESSIONS.md) for closure details and reproductions.

Pipeline/package version and its lock advance to 0.1.1 without upgrading external
dependencies. The publisher also rejects corrupt canonical fingerprints/counts,
retains canonical inputs, stages output before replacing known files, and removes
stale demographics only for an explicit missing-Census publication. The combined
checker/Backend environment is pinned in `tests/integration/backend-tests.lock.txt`;
Data keeps its separate `pipeline/uv.lock`.

## Remaining release work

1. **Rebuild real artifacts with 0.1.1.** The committed Data reports describe
   0.1.0 and are now explicitly historical. Their release-version gate remains
   blocked. Full canonical Parquet and frozen predictions are gitignored and
   absent here; corrected raw metrics, assessment totals, normalization and real
   boundary coverage cannot be verified without them. Rebuild tables before
   training/publishing, then freeze the Backend serving release.
2. **Complete category review.** The 160 sampled candidate/ambiguous/lookalike
   records still have no completed accept/reject verdicts. Resolve the individual
   provider versus facility policy before accepting service-supply counts.
3. **Run the integrated UI and presentation checks.** The latest main commit
   adds a Three.js city simulator under `simulator/`; it loads its own generated
   city binaries. The configured 16 planner browser flows were type-checked and
   collected earlier, but their Backend integration remains unverified. Actual
   planner behavior, source/privacy/zoning/hypothetical labels, economic scenario
   descriptions and presentation evidence remain pending.

The aggregate arithmetic from Data version
`0.1.0+2026-11-14.2+ad464d957df7` is retained only as historical reconciliation.
It does not measure the fixed pipeline. Earlier SF results were already inspected;
disclose that history and freeze evaluation before claiming final-test independence.
No live-data release or product browser experience is approved.

The synthetic API still exercises all five categories, with school FTE deliberately
unavailable and a hypothetical parcel. That is the only demo fallback exercised.
`current/` controls current acceptance; `fixture/` and `synced-main/` are earlier
milestone snapshots. Exact commands are in
[the checker README](../../tests/integration/README.md).
