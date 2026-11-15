# API and artifact handoff

Use [OpenAPI](openapi.json) for field types and [fixture examples](examples/) for integration. Start with parcel `sf:fixture-001` and send slider values as `objective_weights`. All three weights default to one. Priorities default to one for each category and accept values from zero through two. The fixtures omit school operating FTE so economics drops out of the common ranking axes even when other categories have economic ranges.

A comparison returns the applicable versions, scores, reasons, freshness, and a shared clipped 7 m illustrative footprint. `active_dimensions` lists the objectives available across all five categories. The effective weight may be zero on an active dimension. Pareto uses the available dimensions with no weights. When no positively weighted objective is available, all ranks and totals are null and the response gives a reason. A total is in ranking points and can exceed 100. Use `hypothetical`, `vacancy_status`, `source.freshness` and `scoring_reason` from parcel details for display.

## Person 2 release inputs

Publish immutable canonical tables with a source manifest and a `backend_manifest.json` serving descriptor. The builder accepts the following projection of the canonical artifacts. Extra columns are allowed.

| File | Required input |
|---|---|
| `parcels.geoparquet` | One row per `parcel_id` (`sf:<physical lot>`), list of string `source_apns`, `vacancy_status` (`source_indicated_vacant`, `unknown`, `not_vacant`), and an active GeoParquet Polygon or MultiPolygon column with CRS. Optional fields are a GeoParquet `representative_point`, `existing_use`, `zoning`, string `source_vintage`, `assessed_land_usd`, `assessed_improvement_usd`, and `assessment_year`. |
| `places.parquet` | One row per string `place_id`, WGS84 `longitude` and `latitude`, `primary_category`, list of `service_tags`, and `closure_status` (`active`, `unknown`, `closed_temporarily`, `closed_permanently`). Null closure status maps to unknown. Confirmed closures are excluded. |
| `demographics.geoparquet` | One row per string `geoid` with `population` and an active geometry column with CRS. Optional GeoParquet `representative_point` column. Use mutually exclusive geographies to avoid double-counting residents. Null population is omitted and zero population is retained. |
| `review_predictions.parquet` | Optional. One row per `parcel_id` and `category_id`, with nullable `predicted_log1p_reviews_count` and `source` (`model`, `category_baseline`, or `unavailable`). |

The descriptor has `data_version`, `parcel_source_version`, ISO retrieval timestamp `parcel_retrieved_at`, a flat `sources` object mapping names to URLs or release identifiers, a flat `coverage` object with counts or descriptions, and `spatial_coverage` as a WGS84 GeoJSON Polygon or MultiPolygon geometry. Include `model_version` when supplying frozen predictions. Use `hypothetical_parcel_id` to choose a verified-geometry site if the release contains no source-indicated vacancy. The builder retains the full snapshot for picking and calculates percentiles across eligible parcel/category rows.

Spatial coverage represents jointly verified population and service coverage. It should include data across administrative boundaries and around the scoring area. Use null if coverage cannot be attested. Need considers positive-population points at least 1,600 m inside the coverage boundary when setting the target. A local point outside this supported area makes the parcel's need metric unavailable because a facility denominator can depend on population up to two catchments away. Sustainability excludes a nearest-service baseline if the distance crosses the verified coverage boundary. Missing and censored populations are reported separately.

The release freezes per-category 2SFCA targets, per-metric percentile references and the scored metrics. Economics uses P10/P50/P90 of 1,000 seeded draws and the P50 of both tax and FTE for the score. Travel savings use low/base/high sensitivity assumptions. The base avoided vehicle miles per day is the sustainability benefit for ranking.
