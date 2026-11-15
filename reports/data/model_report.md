# Review-activity model report

Data version: `0.1.0+2026-11-14.2+6cbf713c49ee`  
Estimator: sklearn.ensemble.HistGradientBoostingRegressor (scikit-learn 1.5.2)  
Census covariates skipped: True

## Labeled sample sizes

| category | Oakland (train) | Berkeley (validation) | SF (test) |
|---|---|---|---|
| clinic | 1970 | 699 | 5693 |
| pharmacy | 50 | 17 | 119 |
| grocery | 235 | 49 | 413 |
| bank_credit_union | 75 | 23 | 261 |
| school | 0 | 0 | 0 |

Model categories (≥100 Oakland, ≥30 Berkeley): ['clinic', 'grocery']  
Unsupported: ['pharmacy', 'bank_credit_union', 'school']  
Degenerate target (all labeled counts are 0, treated as unlabeled): ['school']

## Selection (Berkeley)

| params | macro RMSE |
|---|---|
| {'max_leaf_nodes': 15, 'l2_regularization': 0.0} | 1.661 |
| {'max_leaf_nodes': 15, 'l2_regularization': 1.0} | 1.649 |
| {'max_leaf_nodes': 31, 'l2_regularization': 0.0} | 1.664 |
| {'max_leaf_nodes': 31, 'l2_regularization': 1.0} | 1.666 |
| category baseline | 1.823 |

Gate (≥5% better than category baseline): **passed** — relative improvement 0.096. Selected: **model**.

## SF test (evaluated once, refit on Oakland + Berkeley)

| category | n | selected RMSE | selected Spearman | category baseline RMSE | city×category baseline RMSE |
|---|---|---|---|---|---|
| clinic | 5693 | 1.060 | -0.091 | 0.987 | 0.987 |
| pharmacy | 119 | — | — | 1.769 | 1.769 |
| grocery | 413 | 2.157 | 0.130 | 2.033 | 2.033 |
| bank_credit_union | 261 | — | — | 1.675 | 1.675 |
| school | 0 | — | — | — | — |

## Slices

### Berkeley

| slice | n | RMSE | mean within-category Spearman |
|---|---|---|---|
| median_household_income_usd | — | unavailable (no training values) | |
| population_density_per_km2 | — | unavailable (no training values) | |

### SF

| slice | n | RMSE | mean within-category Spearman |
|---|---|---|---|
| median_household_income_usd | — | unavailable (no training values) | |
| population_density_per_km2 | — | unavailable (no training values) | |

## Representative failures (SF, largest absolute log-count errors)

| place_id | category | actual | predicted |
|---|---|---|---|
| ChIJs4Hdec5_j4ARyQ1Fsn83VSw | clinic | 6.870 | 0.314 |
| ChIJEyanMISAhYARVYJJPDkRukE | clinic | 6.642 | 0.087 |
| ChIJrR9J1h9-j4ARjEhBVouRQV8 | clinic | 6.472 | 0.040 |
| ChIJK-fsXvN_j4ARuAeLcyc5ZKM | grocery | 0.000 | 6.064 |
| ChIJs6siTQB9j4ARs3lFWB7BnuA | grocery | 0.000 | 6.048 |
| ChIJV-U2io6AhYARJ7UCNhdK_CI | clinic | 6.782 | 0.780 |
| ChIJqRhcpY6AhYARwQ-zDkS9q10 | clinic | 6.284 | 0.530 |
| ChIJMdstMx5-j4ARTiRtCtd6LFg | grocery | 7.475 | 1.787 |
| ChIJAQqeDLaAhYARuOXiMtTshz0 | clinic | 6.513 | 0.897 |
| ChIJwQ93q9aAhYARbkbasCMwYvo | clinic | 6.138 | 0.564 |

The prediction is a review-activity indicator (log1p of Google review count), not a measure of demand, quality or need.
