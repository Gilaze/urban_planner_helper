# Person 4 regression handoff

Audited source baseline: `8898718` on `main`. Owner of the four product fixes:
**Data / Person 2**. These are independently reproduced runtime failures, using
small synthetic inputs through production functions. No actual SF labels were
re-evaluated. Product code is left with its owner, per the implementation plan.

Reproduce all four with the locked Data environment:

```sh
uv sync --project pipeline --locked
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 LOKY_MAX_CPU_COUNT=1 \
  pipeline/.venv/bin/python -m tests.integration.pipeline_probes \
  --output reports/verification/pipeline-probe-results.json
```

The command intentionally exits 1. Its JSON records exact source/config/lock
fingerprints, expected behavior and affected categories. Current result: four
passes and four failures. Passing unit tests do not override these failures.

## P4-DATA-001: SF labels change training eligibility (P1)

Probe: `pipeline.sf_label_isolation`.
Source: [model.py:162](../../pipeline/src/parcel_pipeline/model.py#L162).

The degenerate-target filter examines labels across every city before splitting.
In a synthetic school example, changing only SF labels changes the number of
eligible Oakland training rows from **0 to 3**, and removes school from the
degenerate-category set. SF is supposed to be held out from all training and
selection decisions.

Expected: freeze eligibility/target-quality rules using permitted training data,
then apply them to validation and test. Changing test labels must leave the
training rows, normalization, categories and selected approach unchanged.
Current impact is demonstrated for school and applies to any category whose
degeneracy decision depends on SF targets. Release blocker; confidence high.

## P4-DATA-002: evaluation omits the fallback actually served (P1)

Probe: `pipeline.serving_fallback_evaluation`.
Sources: [model.py:215](../../pipeline/src/parcel_pipeline/model.py#L215),
[model.py:226](../../pipeline/src/parcel_pipeline/model.py#L226).

When the learned model wins, evaluation uses its NaN predictions for unsupported
categories. `predict_parcels` instead substitutes their category baseline. In a
synthetic supported-clinic/unsupported-pharmacy case, production serves pharmacy
baseline **2**, while the selected SF evaluation has **0 of 3 predictions**.
The committed aggregate report likewise leaves pharmacy/bank selected metrics
empty while serving those categories via baselines.

Expected: evaluate the same model-plus-baseline policy that is served, on common
rows, and keep genuinely unavailable categories explicitly null. Export complete
selected predictions for those rows and recompute metrics without changing model
selection based on SF outcomes. Release blocker; confidence high.

## P4-DATA-003: partial condo assessments appear complete (P2)

Probe: `pipeline.partial_assessment`.
Source: [sf.py:292](../../pipeline/src/parcel_pipeline/sources/sf.py#L292).

The fixture's physical lot has two APNs, including an unknown improvement value.
The pipeline reports **USD 200,000** as the parcel improvement total by summing
only known members. There is no partial-value label; matching an assessor row
does not imply all its assessment fields are known.

Expected: propagate an unknown member value to a null physical-lot total, or
export an explicitly named known subtotal with value-level coverage and ensure
the API/UI labels it partial. Preserve actual zeros and assessment vintage.
This affects assessor context, not a demonstrated causal economic estimate.
Confidence high; blocks the affected release field until resolved.

## P4-DATA-004: publication retains pre-filter file checksums (P1)

Probe: `pipeline.publication_fingerprint`.
Source: [cli.py:366](../../pipeline/src/parcel_pipeline/cli.py#L366).

`publish` filters/rewrites parcel and prediction Parquet files, then copies the
canonical `source_manifest.json` unchanged. The published parcel bytes do not
match its declared SHA256. Its original row counts can also describe the
unfiltered universe. Backend schema loading can pass despite this broken
provenance chain.

Expected: fingerprint the actual published bytes and counts, preserve a separate
reference to canonical-source provenance, and identify omissions/release lineage.
Both the canonical and serving projections should validate against their own
manifests. Release blocker; confidence high.

## Remaining evidence and next owners

1. **Data:** fix the four issues, regenerate/freeze canonical and serving outputs,
   and provide the full canonical directory to the checker. The latest committed
   reports declare a successful Census-backed build, but the ignored Parquet and
   frozen prediction files are not present in this checkout. Aggregate arithmetic
   is reconciled; raw metrics, joins and real boundary coverage remain unverified.
   Include fingerprinted `model_normalization.json` evidence as documented in
   `tests/integration/README.md`.
2. **Data + Checker:** review category policy and the 160 sampled candidate,
   ambiguous and clinic/grocery-lookalike records. All verdicts are currently
   empty. Individual provider listings versus service facilities need an explicit
   decision before supply counts are accepted.
3. **Frontend:** provide the runnable UI. The 16 browser cases are type-checked
   and collected, but no product UI exists here to execute them against. Then
   Checker runs both presentation sizes, error states and the actual user flow.
4. **Team + Checker:** disclose earlier SF evaluations, freeze the evaluation
   protocol, and complete the presentation-machine review. SF results were already
   visible in earlier and current report versions; do not describe a later run as
   an untouched holdout or tune against its labels and still claim final-test
   independence.

The six actual Backend provider probes pass, including stale fallback and changed
or new parcels losing cached scores. That former blocker is closed. Python 3.11
execution is also verified. The current fallback exercised is the synthetic API;
no live-data or browser release is approved.
