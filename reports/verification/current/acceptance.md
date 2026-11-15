# Parcel Planner verification

Generated: 2026-11-15T06:07:45.504577+00:00

Mode: **fixture**. Release accepted: **False**.

24 PASS, 3 FAIL, 2 BLOCKED, 1 WARN.

Synthetic checker fixtures only; this does not validate an application or real data release.

## PASS: checker.hand_calculations

Owner: Checker. Impact: checker integrity.

........................................                                 [100%]
40 passed in 0.03s

Reproduce: `python -m tests.integration.verify_release`

Expected: All independent hand-calculated scoring oracle checks pass.

## BLOCKED: pipeline.manifest

Owner: Data. Impact: release blocker.

[Errno 2] No such file or directory: 'data/artifacts/release/source_manifest.json'

Reproduce: `python -m tests.integration.verify_release --artifact-format pipeline --artifact-dir 'data/artifacts/release'`

Expected: Full canonical pipeline release, not just serving projection.

## BLOCKED: data_reports.release_version

Owner: Data. Impact: release blocker.

Reports describe 0.1.0+2026-11-14.2+ad464d957df7; fixed pipeline is 0.1.1. Old reports are historical until outputs are rebuilt.

Reproduce: `python -m tests.integration.verify_release --data-reports-dir 'reports/data'`

Expected: Regenerate release artifacts and reports with the current pipeline.

## PASS: data_reports.gate_arithmetic

Owner: Data. Impact: reported aggregate consistency.

Reported per-category RMSE, macro averages, support minimums and Berkeley gate reconcile. This is aggregate arithmetic, not verification of raw predictions.

Reproduce: `python -m tests.integration.verify_release --data-reports-dir 'reports/data'`

Expected: Reported gate is internally consistent.

## PASS: data_reports.census

Owner: Data. Impact: release blocker.

Data report declares census_skipped=False; verify canonical population/coverage before live use.

Reproduce: `python -m tests.integration.verify_release --data-reports-dir 'reports/data'`

Expected: Census-backed population and covariates exported with margins of error.

## WARN: data_reports.sf_generalization

Owner: Data. Impact: model interpretation.

On reported common SF categories ['clinic', 'grocery'], selected macro RMSE=1.551121, baseline=1.509768. Raw prediction files are unavailable in this checkout; these are reported aggregates, not a fresh SF evaluation.

Reproduce: `python -m tests.integration.verify_release --data-reports-dir 'reports/data'`

Expected: Describe held-out performance and keep SF out of subsequent model selection.

## FAIL: data_reports.category_review

Owner: Data + Checker. Impact: release blocker.

160 sampled rows; 80 accepted, 45 rejected, 35 need owner review. Verdicts cover supplied listing labels, not independent facility verification or population accuracy.

Reproduce: `python -m tests.integration.verify_release --data-reports-dir 'reports/data'`

Expected: Review mappings against source evidence, resolve rejects, and record accept/reject verdicts.

## PASS: pipeline.dotenv_precedence

Owner: Data. Impact: release blocker.

Synthetic environment override, duplicate-line precedence and export/quote parsing verified without disclosing credentials.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Existing environment wins; later dotenv duplicates win without logging values.

## PASS: pipeline.census_error_privacy

Owner: Data. Impact: release blocker.

Network, HTTP and non-JSON Census failures omit credential-bearing URLs, including formatted traceback context.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Request failures never expose the Census key or request URL.

## PASS: pipeline.spatial_features

Owner: Data. Impact: release blocker.

Hand distances 300/700/1500m, closure/focal exclusion, transit complement and predictor names agree.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Focal/closure exclusion and hand-calculated metric radii.

Affected categories: clinic, grocery.

## PASS: pipeline.train_normalization

Owner: Data. Impact: release blocker.

40 Oakland-only synthetic targets: mean=std=log(2); estimator normalization agrees.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Train-only mean/std and zero-variance convention.

Affected categories: clinic.

## PASS: pipeline.sf_label_isolation

Owner: Data. Impact: release blocker.

SF labels cannot affect pre-test category eligibility.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Changing SF test labels never changes Oakland/Berkeley training eligibility.

Affected categories: school.

## PASS: pipeline.serving_fallback_evaluation

Owner: Data. Impact: release blocker.

Evaluation and served unsupported-category fallback use identical rows/predictions.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Evaluation includes the baseline fallback actually served for unsupported categories.

Affected categories: pharmacy.

## PASS: pipeline.partial_assessment

Owner: Data. Impact: release blocker.

Unknown condo member assessment propagates null to the physical-lot total.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Unknown APN values are null at physical-lot grain, or explicitly labeled partial subtotals.

## PASS: pipeline.publication_fingerprint

Owner: Data. Impact: release blocker.

Published-file fingerprints match the rewritten projection.

Reproduce: `pipeline/.venv/bin/python -m tests.integration.pipeline_probes`

Expected: Published manifests contain hashes and row counts for the published bytes.

## PASS: api.health

Owner: Backend. Impact: API contract.

Ready fixture API; versions {'schema_version': '1.0', 'data_version': 'synthetic-sf-1', 'model_version': 'synthetic-review-1', 'assumption_version': 'synthetic-economics-1'}.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Ready API with explicit mode and loaded versions.

## PASS: api.metadata

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Five categories, defaults, units, sources and coverage.

## PASS: api.parcels

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Capped, deduplicated parcels with freshness and source version.

## PASS: api.services

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Active deduplicated supply with category filtering.

## PASS: api.parcel_detail

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Canonical parcel detail with vacancy/hypothetical labeling.

## PASS: api.comparison

Owner: Backend. Impact: API contract.

Default/repeat/reweight/unavailable-weight/reset pass; cached timings [3.28, 2.53, 2.81, 2.32] ms.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Independent ranking/Pareto, unavailable axes, clipped massing and deterministic cached comparisons.

## PASS: api.errors

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Structured unknown-parcel and invalid bbox/weight/priority errors.

## PASS: api.oracle.sf:fixture-001.clinic

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Raw need/travel agrees with independently projected fixture source records.

## PASS: api.oracle.sf:fixture-001.pharmacy

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Raw need/travel agrees with independently projected fixture source records.

## PASS: api.oracle.sf:fixture-001.grocery

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Raw need/travel agrees with independently projected fixture source records.

## PASS: api.oracle.sf:fixture-001.bank_credit_union

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Raw need/travel agrees with independently projected fixture source records.

## PASS: api.oracle.sf:fixture-001.school

Owner: Backend. Impact: API contract.

Published schema 1.0 check passed.

Reproduce: `python -m tests.integration.verify_release --api-base-url 'http://127.0.0.1:8765/api/v1'`

Expected: Raw need/travel agrees with independently projected fixture source records.

## FAIL: release.browser

Owner: Frontend + Checker. Impact: release blocker.

Imported reports/verification/current/browser-results.json; acceptance=False.

Reproduce: `python -m tests.integration.verify_release`

Expected: All required product checks pass, without skips or checker-only stand-ins.

## PASS: release.provider

Owner: Backend + Checker. Impact: release blocker.

Imported reports/verification/production-provider-results.json; acceptance=True.

Reproduce: `python -m tests.integration.verify_release`

Expected: All required product checks pass, without skips or checker-only stand-ins.

## FAIL: release.presentation_review

Owner: Checker. Impact: release blocker.

Evidence file reports/verification/current/manual-evidence.json; acceptance=False.

Reproduce: `python -m tests.integration.verify_release`

Expected: Recorded visual/source review on the presentation machine with inspectable evidence.
