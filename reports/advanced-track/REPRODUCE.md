# Reproduce the submission evidence

Use Python 3.11 and the committed `pipeline/uv.lock` (scikit-learn 1.5.2). All scoring uses existing project inputs; the final-holdout module makes no network calls and never fits or tunes a model.

## Required inputs

- `demo/parcel-planner-data-0.1.1.zip`, already in Git. This exact team release contains the original fitted model, canonical Places table and model report.
- Organizer-provided `google_maps.csv`, stored as `data/raw/places/google_maps.csv`. Its SHA256 must be `47cdc7f87edf37ed6b61b6e0fcb0d4472cc03e9a2b9cd3b2eb1c7cb5774e597b`. Original raw data is not redistributed in this evidence folder.
- The project's existing Census caches: `data/raw/census/tl_2024_06_place.zip`, `tl_2024_06_tract.zip`, and `acs_2024_tract.csv`. Their exact SHA256 values are recorded in `protocol.json`. The same TIGER 2024 and ACS 2024 sources, variable names and download code are in `pipeline/src/parcel_pipeline/sources/census.py`. If absent, the project's documented `parcel-pipeline fetch-census` retrieves those sources (requires a Census key); do not silently substitute a different vintage or ignore a hash mismatch.

The provided Reviews CSV is needed for the original full preprocessing workflow, but not this evaluation: the prediction target is Places review count, not sampled review text/count. No API key is needed with the existing cached Census inputs.

## Raw source to final test metrics

From the repository root:

```bash
cd pipeline
uv sync --frozen
uv run python -m parcel_pipeline.final_holdout --from-raw --output ../data/artifacts/holdout-reproduction
uv run python -m parcel_pipeline.verify_holdout
```

Choose an empty output directory. This is an explicitly labeled **reproduction of an already exposed test**, not another fresh holdout. The script refuses to overwrite a populated output. It:

1. Checks the frozen code/input hashes and the original model/report members in the team ZIP.
2. Rebuilds and cleans Places from the original raw CSV, spatially assigns the original split cities, and verifies the model-relevant columns match the frozen canonical table.
3. Reconstructs the preregistered outside-city cohort, ACS covariates and the same spatial feature columns. As in original training, the focal place is excluded from its supply counts.
4. Checks unique IDs, sufficient learned-category coverage and zero overlap with all original split-city rows.
5. Loads the existing fitted model; uses its original parameters/categories and frozen baseline values. Does not call fit/train/select.
6. Exports row predictions, cohort IDs, category metrics, subgroup metrics and failure examples. First-run files remain in `reports/advanced-track/final-holdout/`.

The raw-data reproduction was run and produced exactly the first-run prediction SHA256 `71a0b008d6c95eb469df78944e1cca2d1b281fa860c3aa333f1b2f09df58629e`. Metrics, rows and cohort hashes also match. Timestamp metadata may differ. `verify_holdout` separately recomputes RMSE using Python's CSV reader and arithmetic, independent of the evaluator's pandas/numpy metric routine.

## Reproduce original model training

The complete original raw-data training workflow remains in `pipeline/README.md`: supplied Places/Reviews CSVs → source adapters → city/tract joins and spatial features → Oakland training → Berkeley grid selection → Oakland+Berkeley refit. A dependency lock, seed, feature list, grid, target-quality policy, category eligibility, baseline implementation and frozen trained binary are included in the project. Use a separate data directory and reports copy for training reproduction so that it does not overwrite historical evidence or the demo release. The historical SF results are exposed; rerunning that workflow does not make them untouched.

This package reproduces new reported metrics from raw Places and documented Census inputs against the exact original fitted model. It does not claim the original raw organizer CSV is public, that API credentials are bundled, or that new source downloads will silently match a historical snapshot. Judges must receive/access the supplied raw input files to run the raw reproduction.
