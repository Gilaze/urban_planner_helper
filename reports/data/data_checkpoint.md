# Data checkpoint

Built: 2026-11-15T07:09:36+00:00

## Sources

| Source | Vintage | Rows | Retrieved |
|---|---|---|---|
| acs_block_group | 2020-2024 ACS 5-year | 3204 | 2026-11-15T05:44:15+00:00 |
| acs_tract | 2020-2024 ACS 5-year | 1102 | 2026-11-15T05:44:17+00:00 |
| google_places | hackathon export | 147429 | 2026-11-15T07:10:16+00:00 |
| google_reviews | hackathon export | 213536 | 2026-11-15T07:10:16+00:00 |
| sf_assessor | 2025 | 211547 | 2026-11-15T07:07:30+00:00 |
| sf_height_districts | 1794658037 | 5292 | 2026-11-15T07:06:42+00:00 |
| sf_landuse | 2023 | 154476 | 2026-11-15T07:06:21+00:00 |
| sf_landuse_current | 2026-08-17T00:00:00.000 | 153765 | 2026-11-15T07:07:07+00:00 |
| sf_parcels | 1794680379 | 227968 | 2026-11-15T07:06:12+00:00 |
| tiger_block_group | TIGER/Line 2024 |  | 2026-11-15T05:44:10+00:00 |
| tiger_place | TIGER/Line 2024 |  | 2026-11-15T05:44:10+00:00 |
| tiger_sf_water | TIGER/Line 2024 |  | 2026-11-15T06:39:12+00:00 |
| tiger_tract | TIGER/Line 2024 |  | 2026-11-15T05:44:10+00:00 |

## Parcels

- Grain: {'parcels_input': 'blklot (APN)', 'parcels_output': 'mapblklot (physical lot)', 'landuse': 'mapblklot', 'assessor': 'parcel_number x closed_roll_year'}
- API records 227,968; dropped for missing blklot/mapblklot 262
- Active APNs 227,706 -> physical lots 155,028 (9,466 multi-APN)
- Geometry quality: {'valid': 155028}
- Land use matched lots: 154,177; land-use rows without an active lot: 298
- Vacancy status: {'not_vacant': 148285, 'source_indicated_vacant': 3810, 'unknown': 2933}
- Vacancy evidence: {'current snapshot records use': 144806, '2023 VACANT code and no recorded use in current snapshot': 3810, '2023 VACANT but current snapshot records use (developed since)': 3187, 'no recorded use in current snapshot; no explicit vacancy code': 915, '2023 VACANT code but lot is mostly under water (submerged)': 772, 'no land-use record': 632, '2023 VACANT code; lot absent from current snapshot': 614, '2023 land use records use; lot absent from current snapshot': 292}
- Current land use (c5ge-t6pj) matched lots: 153,490
- Height districts: 153,144 lots with a limit, 510 in OS (open space), 848 unmatched
- Submerged lots (>= 50% Census water): 817; removed from vacant candidates: 772
- Assessor roll ['2025']: matched APNs 211,163, unmatched active APNs 16,543, roll rows without active APN 384

## Places and reviews

- Input rows 147,429; rejected {'missing_place_id': 0, 'invalid_coordinates': 0, 'invalid_reviews_count_set_null': 0}; duplicates removed 0
- Closure status: {'active': 132143, 'closed_permanently': 12167, 'closed_temporarily': 3045, 'unknown': 74}
- reviews_count missing 33; zero 85,000
- Address city vs spatial city disagreements: 101
- Reviews: 213,536 rows over 6,108 place IDs; matched 139, unmatched 5,969; invalid ratings 42,184

### Primary category by city

| category | Berkeley | Oakland | San Francisco | outside split cities |
|---|---|---|---|---|
| alternative_finance | 13 | 120 | 177 | 2,022 |
| bank_credit_union | 25 | 84 | 338 | 1,532 |
| childcare | 94 | 261 | 857 | 5,426 |
| clinic | 734 | 1,969 | 5,800 | 23,417 |
| dental | 179 | 406 | 1,518 | 8,788 |
| food_assistance | 8 | 38 | 103 | 343 |
| grocery | 66 | 292 | 528 | 2,239 |
| hospital | 29 | 122 | 286 | 1,188 |
| library | 7 | 18 | 39 | 260 |
| mental_health | 1,062 | 1,514 | 4,068 | 12,561 |
| other | 856 | 2,285 | 6,240 | 29,132 |
| pharmacy | 26 | 104 | 215 | 1,346 |
| school | 49 | 179 | 288 | 2,441 |
| social_services | 26 | 119 | 225 | 822 |
| transit | 704 | 2,016 | 4,227 | 17,598 |

## Features

```json
{
  "place_rows_by_split_category": {
    "bank_credit_union": {
      "test": 261,
      "train": 75,
      "validation": 23
    },
    "clinic": {
      "test": 5585,
      "train": 1905,
      "validation": 683
    },
    "grocery": {
      "test": 413,
      "train": 236,
      "validation": 49
    },
    "pharmacy": {
      "test": 119,
      "train": 50,
      "validation": 17
    },
    "school": {
      "test": 253,
      "train": 162,
      "validation": 43
    }
  },
  "place_labeled_rows": 9872,
  "parcel_rows": 775140,
  "parcels_without_rep_point": 0,
  "covariate_missing_rate_parcels": {
    "population_density_per_km2": 0.0,
    "median_household_income_usd": 0.006605258404933302,
    "median_gross_rent_usd_month": 0.01686147018603091
  }
}
```
