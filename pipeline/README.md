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
uv run parcel-pipeline publish            # backend projection -> artifacts/person2
uv run pytest                             # fixture tests, no network
```

Without a Census key, `uv run parcel-pipeline all --skip-census` still builds every
table. Demographic covariates are then null, the missingness flags are set, and
`source_manifest.json` records `census_skipped: true`. Do not ship that release.

Downloads are cached in `data/raw/`. Artifacts go to `data/artifacts/release/`.
Both directories are gitignored. Reports are written to `reports/data/`.

Pipeline **0.1.1** fixes the four Person 4 regressions. Rebuild canonical tables
before training/publishing: older assessor totals and evaluation exports are not
updated by installing the code. With the existing source/Census caches present,
run `parcel-pipeline build`, then `train`, then `publish`. The committed 0.1.0
reports remain historical until that rebuild; no actual SF test was rerun as part
of the code fixes.

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
| SF height districts | `/resource/h9wh-cg3m.json` | district polygon | Zoning Map – Height and Bulk Districts. Joined to each lot's representative point: `height_district`, `height_limit_ft`/`_m`. Repeated-digit codes (2222 … 9999: OS, Candlestick Point, Hunters Point, Special Height Districts) are not heights; those lots get `height_special_plan` and a null limit. |
| SF land use, current | `/resource/c5ge-t6pj.json` | `mapblklot` (`geography_type = parcel`) | Updated 2026-08-17. Floor area per use and residential units. No explicit vacancy code. The v3 `query.json` endpoint needs an app token; the v2.1 `/resource` endpoint is keyless. |
| SF land use 2023 | `/resource/fdfd-xptc.json` | `mapblklot` | Archived. Explicit `VACANT` code on 8,512 lots. One duplicate key. |
| Places | Bright Data CSV | `place_id` | 147,429 rows, no duplicate IDs. No city column (city assigned spatially from TIGER places). No scrape timestamp. |
| Reviews | Bright Data CSV | review | ~515k rows. Only `place_id`, `review_rating` and `review_date` are read. Reviewer identity and text are never loaded. |
| ACS 2024 5-yr | `api.census.gov/data/2024/acs/acs5` | block group, tract | B01003, B19013, B25064 with MOEs. Alameda, Contra Costa, Marin, SF and San Mateo counties for edge coverage. |
| TIGER/Line 2024 | `www2.census.gov/geo/tiger/TIGER2024/{BG,TRACT,PLACE}` | — | `ALAND` for density, place polygons for city assignment. |

**Vacancy rule:** a lot is `source_indicated_vacant` only when it carries the 2023 `VACANT` code AND has no recorded use in the current snapshot. Any current recorded use makes it `not_vacant`, including 3,187 lots coded vacant in 2023 that have since been developed. Lots at least 50% under Census water (SF bay and ocean tidelands) are never vacant candidates; they are `unknown` and flagged `submerged`. Everything else is `unknown`. `vacancy_evidence` gives the reason for each lot.

The two SF Land Use links in the plan resolve to the same dataset. Parcels and
the assessor roll are separate datasets: `acdm-wktn` and `wv5m-vpq2`.

## Canonical outputs (`data/artifacts/release/`)

| File | Grain / key | Contents |
|---|---|---|
| `parcels.geoparquet` | `parcel_id` = `sf:<mapblklot>` | Source APNs (strings), WGS84 polygon, `rep_lon`/`rep_lat`, `area_m2`, `geometry_quality` (valid / repaired / unusable), `existing_use`, `vacancy_status` (`source_indicated_vacant` / `unknown` / `not_vacant`), `site_label` (`source_indicated_vacancy` / `hypothetical`), zoning, assessed values in USD summed across condo APNs with `assessor_roll_year`, and vintages. |
| `places.parquet` | `place_id` | `longitude`/`latitude`, `city` (spatial), `primary_category`, `service_tags`, `closure_status` (`active` / `unknown` / `closed_temporarily` / `closed_permanently`), `is_current_supply`, `reviews_count` (null = unlabeled). |
| `review_summary.parquet` | `place_id` (every Places row) | `sampled_review_count`, `valid_rating_count`, `mean_rating`, first and last review dates, `has_sampled_reviews`. |
| `demographics.geoparquet` | `geoid` × `geo_level` | Population, income and rent with MOEs, `land_area_m2`, `population_density_per_km2`, missingness flags, representative point and vintages. |
| `place_features.parquet` | `place_id` | Shared features plus `split` and target (training records only). |
| `parcel_features.parquet` | `parcel_id` × `category` | Shared features in the same order (`feature_definitions.json`). |
| `review_predictions.parquet` | `parcel_id` × `category_id` | `predicted_log1p_reviews_count` (clamped at 0 or above), `predicted_reviews_count`, `source` (`model` / `category_baseline` / `unavailable`). |
| `tract_covariates.geoparquet` | `geoid` (tract) | Tract covariates for the model. Kept separate so `demographics.geoparquet` (block groups) never double-counts residents. |
| `backend_manifest.json` | — | Serving descriptor for `backend/build_release.py` (contracts/README.md). |
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
- Splits: train on Oakland, select on Berkeley, test once on SF. Rows are training places whose primary group is a candidate and that are not permanently closed. All-zero target categories are identified using Oakland training labels only, then that frozen eligibility rule is applied to the other cities.
- Estimator: `HistGradientBoostingRegressor` with seed 42, lr 0.05, 200 iterations, min leaf 20, no early stopping. Grid: leaves {15, 31} × L2 {0, 1}.
- Model categories need at least 100 labeled Oakland places and at least 30 labeled Berkeley places.
- Gate: macro Berkeley RMSE must be at least 5% better than the category-median baseline. Otherwise the category baseline is shipped.
- Refit on Oakland plus Berkeley, then a single SF evaluation.
- Selected evaluation uses the same model-plus-category-baseline policy as parcel serving. Unsupported categories use their available category baseline; categories with no baseline remain null with `source = unavailable`. Frozen evaluation exports include `prediction_source`.
- Report: log-count RMSE, within-category Spearman, slices by income and density (training-median thresholds, n < 30 marked insufficient), worst errors, sample sizes.

## Backend handoff

`parcel-pipeline publish` writes the contract projection to `artifacts/person2/`, the input for `python -m backend.build_release --input artifacts/person2`. It omits lots with unusable geometry and lots whose key cannot be expressed as a contract `ParcelID`. Today that includes `4591A011*`, whose key contains an asterisk. Census-backed builds populate `spatial_coverage`; an explicitly missing-Census publication removes stale demographics and labels coverage as skipped.

The published `source_manifest.json.outputs` fingerprints the **published** bytes
and row counts. Its `publication` object preserves the canonical manifest hash,
canonical data version/output records, publisher version and omissions. The
canonical files are unchanged. Files are prepared in a staging directory and the
manifest is installed after its referenced files. Canonical checksum/count
mismatches are rejected before altering an existing publication.

Physical-lot assessment totals require a known value for every member APN.
Missing components or unmatched APNs produce null totals; complete zero-valued
components still sum to zero, with the assessment vintage retained.
