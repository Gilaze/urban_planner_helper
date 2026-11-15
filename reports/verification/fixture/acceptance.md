# Parcel Planner verification

Generated: 2026-11-15T04:15:41.451691+00:00

Mode: **fixture**. Release accepted: **False**.

17 PASS, 0 FAIL, 4 BLOCKED, 0 WARN.

Synthetic checker fixtures only; this does not validate an application or real data release.

## PASS: checker.hand_calculations

Owner: Checker. Impact: checker integrity.

.......................................                                  [100%]
39 passed in 0.03s

Reproduce: `python -m tests.integration.verify_release`

Expected: All independent hand-calculated scoring oracle checks pass.

## PASS: artifacts.manifest

Owner: Data. Impact: provenance.

Source attribution, vintage, units and fixture labeling present.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Versioned source manifest with checksums and explicit units.

## PASS: artifacts.parcels

Owner: Data. Impact: data integrity.

1 rows; unique grain ('parcel_id',); null counts {'area_m2': 0, 'existing_use': 0, 'geometry': 0, 'parcel_id': 0, 'representative_point': 0, 'source_apns': 0, 'source_vintage': 0, 'vacancy_status': 0, 'zoning': 1}.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Valid columns, types, keys, coordinates and provenance.

## PASS: artifacts.places

Owner: Data. Impact: data integrity.

20 rows; unique grain ('place_id',); null counts {'city': 0, 'closure_status': 0, 'latitude': 0, 'longitude': 0, 'place_id': 0, 'primary_category': 0, 'reviews_count': 0, 'service_tags': 0}.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Valid columns, types, keys, coordinates and provenance.

## PASS: artifacts.review_summary

Owner: Data. Impact: data integrity.

15 rows; unique grain ('place_id',); null counts {'mean_rating': 0, 'place_id': 0, 'sampled_review_count': 0, 'valid_date_end': 0, 'valid_date_start': 0}.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Valid columns, types, keys, coordinates and provenance.

## PASS: artifacts.demographics

Owner: Data. Impact: data integrity.

1 rows; unique grain ('geoid',); null counts {'geoid': 0, 'geometry': 0, 'income_moe': 1, 'land_area_m2': 0, 'median_gross_rent_usd': 0, 'median_household_income_usd': 1, 'population': 0, 'population_moe': 0, 'rent_moe': 0, 'representative_point': 0, 'source_vintage': 0}.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Valid columns, types, keys, coordinates and provenance.

## PASS: artifacts.place_features

Owner: Data. Impact: data integrity.

20 rows; unique grain ('place_id',); null counts {'category_id': 0, 'place_id': 0, 'split': 0, 'target': 0}.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Valid columns, types, keys, coordinates and provenance.

## PASS: artifacts.parcel_features

Owner: Data. Impact: data integrity.

5 rows; unique grain ('parcel_id', 'category_id'); null counts {'category_id': 0, 'parcel_id': 0}.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Valid columns, types, keys, coordinates and provenance.

## PASS: joins.physical_parcels

Owner: Data. Impact: join cardinality.

APNs assigned to multiple physical parcels: []

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: One physical-lot mapping per source APN.

## PASS: joins.places_reviews

Owner: Data. Impact: coverage.

Left join retains 20 Places rows; 0 unmatched review IDs; 5 Places rows have no sampled reviews.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Review summary is unique before left join; unmatched IDs reported.

## PASS: joins.training_places

Owner: Data. Impact: join cardinality.

Unmatched or recategorized feature IDs: []

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: One feature row maps to one deduplicated Places row.

## PASS: joins.parcel_scenarios

Owner: Data. Impact: comparison coverage.

Missing 0 scenarios; orphan 0 scenarios.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Exactly one row per physical parcel and candidate, including unavailable metrics.

## PASS: coverage.supply

Owner: Data. Impact: coverage.

Observed city/category counts: {'Berkeley:bank_credit_union': 1, 'Berkeley:clinic': 1, 'Berkeley:grocery': 1, 'Berkeley:pharmacy': 1, 'Berkeley:school': 1, 'Oakland:bank_credit_union': 2, 'Oakland:clinic': 2, 'Oakland:grocery': 2, 'Oakland:pharmacy': 2, 'Oakland:school': 2, 'San Francisco:bank_credit_union': 1, 'San Francisco:clinic': 1, 'San Francisco:grocery': 1, 'San Francisco:pharmacy': 1, 'San Francisco:school': 1}; closures: {'open': 20}; 4 multi-tag places.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Manifest city/category and closure counts match canonical Places.

## PASS: coverage.boundaries

Owner: Data. Impact: coverage.

Boundary declaration: 1600 m; city edges preserved: True. This checks the declaration; spatial spot checks are separate.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Population/service coverage includes both 800 m 2SFCA steps at city edges.

## PASS: model.features

Owner: Data. Impact: model integrity.

Predictor order, missingness, spatial parity and focal/closure exclusion checked for 25 rows.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: No review-derived predictors; independently projected neighbor features.

## PASS: model.evaluation

Owner: Data. Impact: model integrity.

Recalculated selection and final metrics separately; selected category_baseline. Common rows: Berkeley 5, SF 5. See model_metrics.json for per-category errors, coverage, worst errors and slices; slices <30 are insufficient.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: Correct splits, train-only transforms/baselines, common rows and model selection gate.

## PASS: scoring.frozen_percentiles

Owner: Backend. Impact: scoring integrity.

Checked 5 frozen SF scenario rows, midpoint ties and economic component averaging.

Reproduce: `python -m tests.integration.verify_release --artifact-dir 'tests/integration/fixtures/artifacts'`

Expected: One versioned fixed reference universe; missing economic components propagate null.

## BLOCKED: release.api

Owner: Backend. Impact: release blocker.

No API base URL supplied.

Reproduce: `python -m tests.integration.verify_release`

Expected: Ready fixture/live API.

## BLOCKED: release.browser

Owner: Frontend + Checker. Impact: release blocker.

No browser results supplied

Reproduce: `python -m tests.integration.verify_release`

Expected: Measured browser checks against the integrated product.

## BLOCKED: release.provider

Owner: Backend + Checker. Impact: release blocker.

No provider results supplied

Reproduce: `python -m tests.integration.verify_release`

Expected: Measured provider checks against the integrated product.

## BLOCKED: release.presentation_review

Owner: Checker. Impact: release blocker.

No presentation-machine evidence supplied

Reproduce: `python -m tests.integration.verify_release`

Expected: Completed presentation-machine evidence; see manual_evidence.example.json.
