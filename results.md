# Parcel Planner model results

## Model and evaluation

We trained scikit-learn’s `HistGradientBoostingRegressor` to predict `log(1 + Google review count)` from nearby services and transit, distance to the closest peer, and Census estimates for population density, income, and rent. Google review counts measure online review activity. The planner ranks proposals separately using service access, economics, and estimated travel savings.

We trained on existing places in **Oakland**, chose model settings on **Berkeley**, and refit on both cities before evaluating on **San Francisco**. Clinics and groceries met the minimum of 100 labeled Oakland places and 30 labeled Berkeley places. We compared the model with two training-set baselines:

1. Median log review count by service category.
2. Median log review count by city and service category, with the category median as the fallback for unseen cities. This fallback made both baselines identical on Berkeley and San Francisco.

## Key metrics

On **Berkeley**, the model’s average RMSE across clinics and groceries was **1.607**, versus **1.831** for the baseline. The **12.2%** reduction cleared the **5%** threshold for selecting the model.

In **San Francisco**, the baseline had lower RMSE for both categories. RMSE measures error on the log review-count scale. Spearman measures how well predictions ranked places within each category.

| SF category | Test places | Model RMSE | Baseline RMSE | Model Spearman |
|---|---:|---:|---:|---:|
| Clinic | 5,584 | 1.091 | **0.993** | −0.065 |
| Grocery | 413 | 2.095 | **2.033** | 0.158 |
| Pharmacy | 119 | — | 1.769 | — |
| Bank / credit union | 261 | — | 1.675 | — |
| School | 0 eligible | — | — | — |

The negative clinic correlation and weak positive grocery correlation show limited ranking ability in SF. The team evaluated SF across multiple builds, so these results describe performance on a previously examined test set.

Pharmacies and banks or credit unions fell short of the minimum sample sizes for the boosted model. They have no model RMSE because the boosted model was never fit for those categories. Their reported RMSEs measure the category-median predictions used in the release. Spearman is undefined for these constant predictions.

All school review counts in the training data were zero, so the pipeline excluded schools from review-activity evaluation. Schools still appear in the planner’s service comparisons.

## Evidence

- **Evaluation results.** See the split sizes, model selection grid, income and density breakdowns, and largest SF errors. [Model report](reports/data/model_report.md) · [Full metrics JSON](reports/data/model_report.json) · [Evaluation history](reports/data/FINDINGS.md)
- **Implementation.** See how the model, baseline fallback, and evaluation metrics are computed and which inputs the model uses. [Model code](pipeline/src/parcel_pipeline/model.py) · [Feature definitions](pipeline/src/parcel_pipeline/features.py) · [Planner ranking code](backend/ranking.py)

