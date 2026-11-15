# Parcel Planner data pipeline

Owner: Data workstream (Person 2). Produces the canonical parcel, Places and
population artifacts plus the review-activity model consumed by `backend/`.

## Run from a clean checkout

```bash
cd pipeline
uv sync                                   # Python 3.11, locked deps (uv.lock)
export PLACES_CSV=/path/to/google_maps.csv
export REVIEWS_CSV=/path/to/google_maps_reviews.csv
export CENSUS_API_KEY=...                 # https://api.census.gov/data/key_signup.html
uv run parcel-pipeline all                # fetch-sf, fetch-census, build, train, audit
uv run pytest                             # fixture tests, no network
```

Without a Census key, `uv run parcel-pipeline all --skip-census` still builds every
table. Demographic covariates are then null, the missingness flags are set, and
`source_manifest.json` records `census_skipped: true`. Do not ship that release.

Downloads are cached in `data/raw/`. Artifacts go to `data/artifacts/release/`.
Both directories are gitignored. Reports are written to `reports/data/`.

| Env var | Default | Purpose |
|---|---|---|
| `PIPELINE_DATA_DIR` | `<repo>/data` | Download cache and artifacts |
| `PLACES_CSV`, `REVIEWS_CSV` | `data/raw/places/…`, `data/raw/reviews/…` | Bright Data exports |
| `CENSUS_API_KEY` | none | ACS 2024 five-year (keyless requests are rejected) |
| `SF_PARCEL_API_BASE` | `https://data.sf.gov` | SF Socrata host (`data.sfgov.org` now 301-redirects here) |
| `SF_APP_TOKEN` | none | Optional Socrata app token |

## Verified sources (checked 2026-11-14)

| Source | Endpoint | Grain | Notes |
|---|---|---|---|
| SF parcels | `/resource/acdm-wktn.json` | `blklot` (APN) | 227,968 active APNs → 155,028 `mapblklot` physical lots. Condos share `mapblklot`. 262 active rows have no shape. |
| SF assessor | `/resource/wv5m-vpq2.json` | `parcel_number` × `closed_roll_year` | `parcel_number` uses the blklot format. The latest roll (2025) is selected automatically. |
| SF land use | `/resource/fdfd-xptc.json` | `mapblklot` | Archived 2023 snapshot. Explicit `VACANT` code on 8,512 lots. One duplicate key. |
| Places | Bright Data CSV | `place_id` | 147,429 rows, no duplicate IDs. No city column (city assigned spatially from TIGER places). No scrape timestamp. |
| Reviews | Bright Data CSV | review | ~515k rows. Only `place_id`, `review_rating` and `review_date` are read. Reviewer identity and text are never loaded. |
| ACS 2024 5-yr | `api.census.gov/data/2024/acs/acs5` | block group, tract | B01003, B19013, B25064 with MOEs. Alameda, Contra Costa, Marin, SF and San Mateo counties for edge coverage. |
| TIGER/Line 2024 | `www2.census.gov/geo/tiger/TIGER2024/{BG,TRACT,PLACE}` | — | `ALAND` for density, place polygons for city assignment. |

The two SF Land Use links in the plan resolve to the same dataset. Parcels and
the assessor roll are separate datasets: `acdm-wktn` and `wv5m-vpq2`.

## Canonical outputs (`data/artifacts/release/`)

| File | Grain / key | Contents |
|---|---|---|
| `parcels.geoparquet` | `parcel_id` = `sf:<mapblklot>` | Source APNs (strings), WGS84 polygon, `rep_lon`/`rep_lat`, `area_m2`, `geometry_quality` (valid / repaired / unusable), `existing_use`, `vacancy_status` (`source_indicated_vacant` / `unknown` / `not_vacant`), `site_label` (`source_indicated_vacancy` / `hypothetical`), zoning, assessed values in USD summed across condo APNs with `assessor_roll_year`, and vintages. |
| `places.parquet` | `place_id` | Coordinates, `city` (spatial), `primary_category`, `service_tags`, `closure_status`, `is_current_supply`, `reviews_count` (null = unlabeled). |
| `review_summary.parquet` | `place_id` (every Places row) | `sampled_review_count`, `valid_rating_count`, `mean_rating`, first and last review dates, `has_sampled_reviews`. |
| `demographics.geoparquet` | `geoid` × `geo_level` | Population, income and rent with MOEs, `land_area_m2`, `population_density_per_km2`, missingness flags, representative point and vintages. |
| `place_features.parquet` | `place_id` | Shared features plus `split` and target (training records only). |
| `parcel_features.parquet` | `parcel_id` × `category` | Shared features in the same order (`feature_definitions.json`). |
| `parcel_predictions.parquet` | `parcel_id` × `category` | `review_activity_log1p`, `review_activity_count`, `review_activity_source` (`model` / `category_baseline` / null). |
| `place_eval_predictions.parquet`, `splits.parquet` | `place_id` | Frozen validation and test predictions, for the Checker to recompute metrics. |
| `model_report.json`, `feature_definitions.json`, `source_manifest.json` | — | Model selection, feature specs, and provenance (URL, retrieval time, vintage, sha256, counts, `data_version`). |

Units: area in m², distance in m, assessed values in USD, rent in USD per month,
density in persons per km². Metric work uses EPSG:26910 and exports use WGS84.

## Categories

`config/categories.yaml` is versioned. It defines 15 groups and matches source
labels exactly (case- and whitespace-insensitive). Name regexes remove lookalikes
such as med spas, pet clinics, and liquor or smoke shops. The primary group comes
from `category` and drives learning. Service tags come from `category` plus
`all_categories` and drive supply. `parcel-pipeline audit` writes
`reports/data/category_audit_sample.csv`: 20 records per candidate, 20 ambiguous
records, and 20 clinic and grocery lookalikes, with an empty `reviewer_verdict`
column for manual review.

## Review-activity model

- Target: `log1p(reviews_count)`, standardized within each category with training mean and std. Predictions are inverted to log-count scale for evaluation.
- Splits: train on Oakland, select on Berkeley, test once on SF. Rows are training places whose primary group is a candidate and that are not permanently closed.
- Estimator: `HistGradientBoostingRegressor` with seed 42, lr 0.05, 200 iterations, min leaf 20, no early stopping. Grid: leaves {15, 31} × L2 {0, 1}.
- Model categories need at least 100 labeled Oakland places and at least 30 labeled Berkeley places.
- Gate: macro Berkeley RMSE must be at least 5% better than the category-median baseline. Otherwise the category baseline is shipped.
- Refit on Oakland plus Berkeley, then a single SF evaluation.
- Report: log-count RMSE, within-category Spearman, slices by income and density (training-median thresholds, n < 30 marked insufficient), worst errors, sample sizes.
