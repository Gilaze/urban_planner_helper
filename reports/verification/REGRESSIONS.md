# Four Data regressions — resolved

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
the combined checker suite has **135 passing tests**, with no skips when the
fixture API and locked Backend environment are available.

## Real artifacts still require rebuilding

The code fixes do not repair cached 0.1.0 Parquet files or retroactively recompute
old evaluation reports. With the source/Census caches present, rebuild canonical
tables, regenerate evaluation/serving outputs, publish the new projection, and
freeze the Backend release. Preserve SF evaluation history rather than claim a
new untouched holdout. The actual cached files are absent from this checkout,
so only synthetic regressions were executed here.

The fixed publisher records `publication.canonical_manifest_sha256`, original
canonical output records, publisher version, omitted files and parcel counts.
Its root `outputs` describe the published bytes, not the canonical inputs.
Canonical hash/count mismatches are rejected before touching an old publication.
An explicit missing-Census publication removes stale demographic files and labels
coverage as skipped; it is not eligible for live release.

Remaining release evidence: rebuilt canonical files/normalization exports, category
review, the real Frontend/browser flow, boundary coverage and presentation review.
Those are separate from these four now-closed code defects. See
[ACCEPTANCE.md](ACCEPTANCE.md) and [current results](current/results.json).
