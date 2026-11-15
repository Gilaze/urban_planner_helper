# Review-activity model report

Data version: `0.1.1+2026-11-15.1+bfa9e1e005fb`  
Estimator: sklearn.ensemble.HistGradientBoostingRegressor (scikit-learn 1.5.2)  
Census covariates skipped: False

## Labeled sample sizes

| category | Oakland (train) | Berkeley (validation) | SF (test) |
|---|---|---|---|
| clinic | 1905 | 683 | 5584 |
| pharmacy | 50 | 17 | 119 |
| grocery | 235 | 49 | 413 |
| bank_credit_union | 75 | 23 | 261 |
| school | 0 | 0 | 0 |

Model categories (≥100 Oakland, ≥30 Berkeley): ['clinic', 'grocery']  
Unsupported: ['pharmacy', 'bank_credit_union', 'school']  
Degenerate target (all Oakland training counts are 0, treated as unlabeled): ['school']

## Selection (Berkeley)

| params | macro RMSE |
|---|---|
| {'max_leaf_nodes': 15, 'l2_regularization': 0.0} | 1.607 |
| {'max_leaf_nodes': 15, 'l2_regularization': 1.0} | 1.633 |
| {'max_leaf_nodes': 31, 'l2_regularization': 0.0} | 1.622 |
| {'max_leaf_nodes': 31, 'l2_regularization': 1.0} | 1.646 |
| category baseline | 1.831 |

Gate (≥5% better than category baseline): **passed** — relative improvement 0.122. Selected: **model**.

## SF test (evaluated once, refit on Oakland + Berkeley)

| category | n | selected RMSE | selected Spearman | category baseline RMSE | city×category baseline RMSE |
|---|---|---|---|---|---|
| clinic | 5584 | 1.091 | -0.065 | 0.993 | 0.993 |
| pharmacy | 119 | 1.769 | — | 1.769 | 1.769 |
| grocery | 413 | 2.095 | 0.158 | 2.033 | 2.033 |
| bank_credit_union | 261 | 1.675 | — | 1.675 | 1.675 |
| school | 0 | — | — | — | — |

## Slices

### Berkeley

| slice | n | RMSE | mean within-category Spearman |
|---|---|---|---|
| median_household_income_usd:low | 189 | 1.223 | 0.063 |
| median_household_income_usd:high | 583 | 1.364 | -0.013 |
| median_household_income_usd:missing | 0 | insufficient (<30) | |
| population_density_per_km2:low | 584 | 1.380 | -0.025 |
| population_density_per_km2:high | 188 | 1.167 | 0.085 |
| population_density_per_km2:missing | 0 | insufficient (<30) | |

### SF

| slice | n | RMSE | mean within-category Spearman |
|---|---|---|---|
| median_household_income_usd:low | 1281 | 1.314 | 0.031 |
| median_household_income_usd:high | 4979 | 1.196 | -0.048 |
| median_household_income_usd:missing | 117 | 1.390 | 0.271 |
| population_density_per_km2:low | 2013 | 1.075 | -0.004 |
| population_density_per_km2:high | 4355 | 1.287 | 0.054 |
| population_density_per_km2:missing | 9 | insufficient (<30) | |

## Representative failures (SF, largest absolute log-count errors)

| place_id | category | actual | predicted |
|---|---|---|---|
| ChIJs4Hdec5_j4ARyQ1Fsn83VSw | clinic | 6.870 | 0.094 |
| ChIJEyanMISAhYARVYJJPDkRukE | clinic | 6.642 | 0.135 |
| ChIJK-fsXvN_j4ARuAeLcyc5ZKM | grocery | 0.000 | 6.483 |
| ChIJV-U2io6AhYARJ7UCNhdK_CI | clinic | 6.782 | 0.382 |
| ChIJrR9J1h9-j4ARjEhBVouRQV8 | clinic | 6.472 | 0.308 |
| ChIJqRhcpY6AhYARwQ-zDkS9q10 | clinic | 6.284 | 0.466 |
| ChIJAQqeDLaAhYARuOXiMtTshz0 | clinic | 6.513 | 0.718 |
| ChIJs6siTQB9j4ARs3lFWB7BnuA | grocery | 0.000 | 5.750 |
| ChIJEVjW9hx-j4ARo0UeqE-SYFg | clinic | 5.953 | 0.274 |
| ChIJ79rp6PSAhYAReQ2527Dn7nY | clinic | 5.684 | 0.267 |

The prediction is a review-activity indicator (log1p of Google review count), not a measure of demand, quality or need.
