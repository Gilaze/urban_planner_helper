# Person 4 regression status

Fixed in pipeline **0.1.1**, following the user's explicit request to implement
the fixes directly. The original failures were independently reproduced at
`f8ba4ba`; the same eight probes now all pass without changing their expected
values. Actual SF test labels were not re-evaluated.

| Issue | Fix | Verification |
| --- | --- | --- |
| P4-DATA-001: SF labels affected training eligibility | Fit all-zero target/category eligibility on Oakland training labels only and freeze it for validation/test. | Changing Berkeley or SF labels cannot change training rows, categories or baselines. |
| P4-DATA-002: selected evaluation omitted served fallbacks | Evaluation and parcel serving share one model-plus-category-baseline function. Export prediction sources; preserve null only when no baseline exists. | A model-winning synthetic case evaluates all three unsupported pharmacy rows with the same baseline value served to parcels. |
| P4-DATA-003: partial condo assessments appeared complete | Any unknown member APN component makes that physical-lot total null. Complete zero values remain zero. | Complete, zero, partially missing, wholly missing and unmatched-APN cases pass. |
| P4-DATA-004: publication copied pre-filter checksums | Stage the serving projection, hash/count the actual published files, preserve canonical manifest lineage and omissions, and install the manifest last. | Published hashes/counts match filtered bytes; repeats are deterministic; canonical files and prior publications survive rejected inputs. |

Sources: [model.py](../../pipeline/src/parcel_pipeline/model.py),
[sf.py](../../pipeline/src/parcel_pipeline/sources/sf.py),
[cli.py](../../pipeline/src/parcel_pipeline/cli.py).

Reproduce the independent proofs:

```sh
uv sync --project pipeline --locked
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 \
  pipeline/.venv/bin/python -m tests.integration.pipeline_probes \
  --output reports/verification/pipeline-probe-results.json
```

Current result: **8 PASS, 0 FAIL**, exit 0. The JSON fingerprints the tested
production code, configuration and lock. The Data suite has **36 passing tests**;
the combined checker suite has **142 passing tests**, with no skips when the
fixture API and locked Backend environment are available.

## Open findings from the repository-only audit

Baseline: `35f3947`. These findings stay with their product owners. The user has
limited further work to Person 4's tests/reports and repository-provided inputs.

### P4-DATA-005 — candidate scope and secondary supply tags (P1)

Owner: **Data / Person 2**. Reproduce with the committed
`reports/data/category_audit_sample.csv` and the exact-source-bound
[Checker review](category-review.csv). All 160 rows were reviewed: 80
label-consistent, 45 rejected, 35 needing owner evidence. This is a listing audit,
not a population error-rate estimate or external facility verification.

All 20 clinic-lookalike rows have a specialist/surgeon primary label mapped to
`other`, yet retain a `clinic` supply tag. All 20 grocery-lookalike rows have
`Convenience store` mapped to `other`, yet retain `grocery`. The taxonomy calls
the clinic candidate primary care and explicitly excludes convenience-store
lookalikes from grocery supply. Resolve how secondary labels establish a genuine
candidate service rather than assume the primary exclusion removes supply.
Individual-provider listings also need a distinct-facility/capacity policy.

Expected: consistent learning/supply eligibility, documented candidate scope and
audited facility handling. Impact: candidate supply counts, spatial features,
need and travel estimates. No taxonomy/product code was changed by Checker.
See [the review rationale and counts](CATEGORY_REVIEW.md).

### P4-FRONTEND-001 — planner flow unavailable on main (P1)

Owner: **Frontend / Person 1**. Run the eight flows in
`tests/integration/browser/parcel-flow.spec.ts` at both configured sizes against
`simulator/`. Result: **0 passed, 16 failed, 0 skipped**. The parcel selector and
comparison controls are absent; the city viewer loads binaries rather than the
planner API. Downstream stale-response, reset, massing and failure handling are
therefore unverified. These are failed entry points, not 16 distinct defects.

Expected: the agreed parcel selection → preview → reweight → reset API flow.
Impact: presentation and end-to-end release acceptance. Evidence and reproduction:
[BROWSER_QA.md](BROWSER_QA.md), [test results](current/browser-results.json).

### P4-FRONTEND-002 — repo-only city assets unavailable (P1 readiness gap)

Owner: **Frontend / Person 1**, with Data for the asset handoff. Start the
simulator without externally generated `public/data`. At both presentation sizes,
`/data/meta.json` returns HTML and JSON parsing fails; the loader shows
“Could not load the city data.” The documented asset generator downloads sources,
which is outside the user's final repository-only restriction.

Expected for this audit: repository-provided assets or an explicit fixture/fallback
that lets the committed UI run. This does not imply the documented full-data
build would fail with its required inputs. Screenshot/console/HTTP evidence is
recorded in [browser-smoke.json](current/browser-smoke.json).

## Real artifacts still require rebuilding

The code fixes do not repair cached 0.1.0 Parquet files or retroactively recompute
old evaluation reports. With the source/Census caches present, rebuild canonical
tables, regenerate evaluation/serving outputs, publish the new projection, and
freeze the Backend release. Preserve SF evaluation history rather than claim a
new untouched holdout. Full permitted canonical/frozen files are absent from the
tracked repository inputs, so only synthetic regressions were executed here.
Inputs acquired before the user's repository-only restriction are excluded from
the active pipeline paths and current evidence.

The fixed publisher records `publication.canonical_manifest_sha256`, original
canonical output records, publisher version, omitted files and parcel counts.
Its root `outputs` describe the published bytes, not the canonical inputs.
Canonical hash/count mismatches are rejected before touching an old publication.
An explicit missing-Census publication removes stale demographic files and labels
coverage as skipped; it is not eligible for live release.

Remaining release evidence: owner-resolved category mappings, rebuilt canonical
files/normalization exports, the integrated Frontend flow, boundary coverage and
presentation readiness. Category review has been performed; its findings remain
unresolved. Those are separate from the four closed code defects. See
[ACCEPTANCE.md](ACCEPTANCE.md) and [current results](current/results.json).
