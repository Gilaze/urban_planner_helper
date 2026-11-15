# Review-activity model report

Data version: `0.1.0+2026-11-14.2+ad464d957df7`  
Estimator: sklearn.ensemble.HistGradientBoostingRegressor (scikit-learn 1.5.2)  
Census covariates skipped: False

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
| {'max_leaf_nodes': 15, 'l2_regularization': 0.0} | 1.607 |
| {'max_leaf_nodes': 15, 'l2_regularization': 1.0} | 1.605 |
| {'max_leaf_nodes': 31, 'l2_regularization': 0.0} | 1.602 |
| {'max_leaf_nodes': 31, 'l2_regularization': 1.0} | 1.578 |
| category baseline | 1.823 |

Gate (≥5% better than category baseline): **passed** — relative improvement 0.134. Selected: **model**.

## SF test (evaluated once, refit on Oakland + Berkeley)

| category | n | selected RMSE | selected Spearman | category baseline RMSE | city×category baseline RMSE |
|---|---|---|---|---|---|
| clinic | 5693 | 1.004 | -0.072 | 0.987 | 0.987 |
| pharmacy | 119 | — | — | 1.769 | 1.769 |
| grocery | 413 | 2.098 | 0.195 | 2.033 | 2.033 |
| bank_credit_union | 261 | — | — | 1.675 | 1.675 |
| school | 0 | — | — | — | — |

## Slices

### Berkeley

| slice | n | RMSE | mean within-category Spearman |
|---|---|---|---|
| median_household_income_usd:low | 193 | 1.169 | 0.019 |
| median_household_income_usd:high | 595 | 1.363 | 0.025 |
| median_household_income_usd:missing | 0 | insufficient (<30) | |
| population_density_per_km2:low | 596 | 1.380 | 0.009 |
| population_density_per_km2:high | 192 | 1.114 | 0.062 |
| population_density_per_km2:missing | 0 | insufficient (<30) | |

### SF

| slice | n | RMSE | mean within-category Spearman |
|---|---|---|---|
| median_household_income_usd:low | 1305 | 1.221 | 0.098 |
| median_household_income_usd:high | 5062 | 1.080 | -0.060 |
| median_household_income_usd:missing | 119 | 1.306 | 0.336 |
| population_density_per_km2:low | 2033 | 0.959 | -0.066 |
| population_density_per_km2:high | 4444 | 1.176 | 0.082 |
| population_density_per_km2:missing | 9 | insufficient (<30) | |

## Representative failures (SF, largest absolute log-count errors)

| place_id | category | actual | predicted |
|---|---|---|---|
| ChIJs4Hdec5_j4ARyQ1Fsn83VSw | clinic | 6.870 | 0.000 |
| ChIJEyanMISAhYARVYJJPDkRukE | clinic | 6.642 | 0.000 |
| ChIJV-U2io6AhYARJ7UCNhdK_CI | clinic | 6.782 | 0.472 |
| ChIJs6siTQB9j4ARs3lFWB7BnuA | grocery | 0.000 | 6.306 |
| ChIJrR9J1h9-j4ARjEhBVouRQV8 | clinic | 6.472 | 0.253 |
| ChIJqRhcpY6AhYARwQ-zDkS9q10 | clinic | 6.284 | 0.129 |
| ChIJAQqeDLaAhYARuOXiMtTshz0 | clinic | 6.513 | 0.365 |
| ChIJ___coZF9j4AR0PperWqavY0 | grocery | 0.000 | 5.492 |
| ChIJ79rp6PSAhYAReQ2527Dn7nY | clinic | 5.684 | 0.262 |
| ChIJYVtXKr2BhYARoWainHUmHUQ | clinic | 5.613 | 0.243 |

The prediction is a review-activity indicator (log1p of Google review count), not a measure of demand, quality or need.
