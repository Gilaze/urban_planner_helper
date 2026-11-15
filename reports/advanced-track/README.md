# Advanced Track submission evidence

The project now has a **new label-held-out geographic evaluation** of the already-trained predictive model. This supplements the exposed San Francisco results; it does not relabel SF as untouched. The evidence addresses the technical checklist supplied by the team. Qualification is decided by the organizers, not guaranteed by this report.

## What to tell the judges

> We trained a histogram gradient-boosted regressor to predict log(1 + Google review count), using the provided Google Maps Places dataset. We selected parameters on Berkeley after training on Oakland. After recognizing that SF had been examined across pipeline versions, we froze the existing model and evaluated previously excluded labels from 10,480 places across 50 other cities. The learned categories achieved macro RMSE 1.873 versus a category-median baseline of 1.919, a 2.4% reduction. We report all category results, four county subgroups, income/density slices and actual failures. The planning interface's need score is a separate rule-based calculation; review activity is not proof of community demand.

## Checklist and evidence

| Requirement | Evidence |
| --- | --- |
| At least one provided dataset | Provided `google_maps.csv` supplies locations, categories and the review-count target. `google_maps_reviews.csv` is used for aggregate coverage/quality checks, not review-text modeling. Public SF/Census/BLS/CDTFA inputs support the broader planner. |
| Train/fine-tune a predictive model | `pipeline/src/parcel_pipeline/model.py`: trained sklearn `HistGradientBoostingRegressor`, seed 42, learning rate 0.05, 200 iterations, selected leaves 15/L2=0. Frozen fitted model is inside the committed original data ZIP. Learned categories: clinic and grocery. |
| Simple baseline | Frozen category-median log review count fitted only on Oakland+Berkeley; compared on exactly the same new test rows. Pharmacy and bank use baseline fallback; school review target remains unavailable. |
| Appropriate held-out evaluation | [Protocol](protocol.json), frozen in commit `cafa5f6` before evaluation, and [first-run results](final-holdout/results.json), preserved in `1082794`. No training, tuning or category changes used the new results. Zero IDs overlap original split cities. |
| Error and fairness analysis | Four geographic subgroups below, fixed-threshold income/density slices and ten concrete failures in `results.json`. Per-category RMSE, MAE and Spearman also reported. |
| Data decisions and sample check | [Existing 160-listing annotated audit](../verification/category-review.csv), [audit explanation](../verification/CATEGORY_REVIEW.md), [Data response](../data/category_review_response.csv), [manual-review record](MANUAL_REVIEW.md). Cleaning, joins and unresolved cases are disclosed. |
| Raw-data reproducibility | [Reproduction instructions](REPRODUCE.md), frozen dependency lock, executable evaluation and independent CSV verification. Raw reconstruction reproduced the first-run prediction CSV **byte for byte**; see [verification](verification.json). Judges need the organizer-provided raw Places file and documented Census inputs. |

## Final holdout design

The old pipeline used only Census city boundaries for Oakland, Berkeley and San Francisco when creating labeled modeling rows. Other locations remained outside those splits. The final holdout includes eligible Places in other incorporated cities, inside supplied ACS tracts in Alameda, Contra Costa, Marin and San Mateo counties. All eligible locations are included; no cities were chosen based on their target values or errors. Permanently closed listings and the already-degenerate school target follow the original policy.

The predictor is the **original frozen binary**, not a newly fitted model. The baseline, learned categories, feature rules, target transformation and subgroup thresholds were fixed before the new scores. The final cohort contains 10,480 distinct place IDs: 8,667 clinic, 781 grocery, 426 pharmacy and 606 bank/credit union listings. The primary comparison uses the two learned categories (9,448 rows), with equal category weight; the full frozen policy additionally covers baseline-fallback categories.

Business coordinates and non-target service tags in these locations were already spatial context for features. Their review-count labels were excluded from the original three-city training/selection/evaluation pipeline. Therefore describe this as **previously excluded labels in known geographic context**, not an entirely unseen source dataset. Previous global taxonomy decisions and the SF evaluation history remain disclosed. After this run the new test is exposed too: do not use it to tune and then call it fresh again.

## Model versus baseline

RMSE and MAE are on the natural-log scale `log(1 + review count)`, not raw reviews. Lower error is better. Spearman measures within-category rank association.

| Category | n | Learned-model RMSE | Baseline RMSE | Learned MAE | Baseline MAE | Learned Spearman |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Clinic | 8,667 | 1.357 | 1.462 | 0.812 | 0.681 | 0.035 |
| Grocery | 781 | 2.389 | 2.376 | 2.033 | 2.062 | 0.112 |
| **Equal-weight mean, learned categories** | **9,448** | **1.873** | **1.919** | — | — | — |
| Pharmacy, baseline fallback | 426 | — | 1.585 | — | 1.449 | — |
| Bank/credit union, baseline fallback | 606 | — | 1.387 | — | 1.030 | — |

The 2.4% macro-RMSE improvement is modest. Grocery RMSE is slightly worse than baseline; clinic MAE is also worse. Rank correlations are weak. We did not change or retune the model after seeing this result. The original Berkeley selection gate was 5%; this new result is a test result, not a new selection gate or a reason to change deployment policy. Schools do not have a usable review target, rather than being assigned invented predictions.

## Four geographic subgroups

These are disjoint county groups **outside the original three cities**. Both columns use the same group and the full frozen model/baseline policy. The JSON also reports learned-model-only metrics and their distinct denominators. Pooled errors can reflect different service-category mixes; these comparisons are an error/fairness investigation, not a certification of fairness or a causal demographic effect.

| County | Test places | Frozen policy RMSE | Baseline RMSE |
| --- | ---: | ---: | ---: |
| Alameda, excluding Oakland/Berkeley | 3,229 | 1.508 | 1.637 |
| Contra Costa | 3,557 | 1.374 | 1.391 |
| Marin | 1,020 | 1.592 | 1.649 |
| San Mateo | 2,674 | 1.498 | 1.604 |

The original training-derived thresholds remain $106,938 for income and 5,583.173 people/km² for density. Low/high income groups have n = 2,906 / 7,573 and policy RMSE 1.382 / 1.502; missing income n = 1 is suppressed. Low/high density groups have n = 9,661 / 818 and RMSE 1.442 / 1.764; missing density n = 1 is suppressed. We never report an error estimate for a subgroup smaller than 30.

## Concrete failures and social-good limits

The first-run JSON preserves the ten largest absolute log-count errors and place IDs. For example, a San Leandro clinic (`ChIJb7m2TcSPj4ARhJfp8b1W6cc`) has actual 8.015 and predicted 0.648; a Concord clinic (`ChIJxUdhG7ZmhYARvVhZX8rSEBs`) has actual 7.299 and predicted 0.002. These are severe underpredictions of high-review listings. They demonstrate that low aggregate error is insufficient for reliable site ordering.

Review activity measures online visibility/engagement, not visits, quality or unmet need. The planner's need/fit ranking is separate. Snapshot vacancy and simplified zoning require confirmation; jobs, sales tax and travel outcomes are scenarios. Individual practitioners may duplicate facilities, review coverage is sparse, and 35 original audit cases still require evidence or re-review. These limitations remain in the submission rather than being hidden to satisfy a checklist.
