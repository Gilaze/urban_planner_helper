# Getting the data

The built data is not in git. It derives partly from the hackathon's Bright Data exports, which should not be in a public repo, and `release.json` is 170 MB. Download the prebuilt bundle instead of rerunning the pipeline.

1. Download `parcel-planner-data-0.1.1.zip` from the team's [DSS FA26 Datathon Drive folder](https://drive.google.com/drive/folders/1iGJ511t7Enpj9ShRZmMOkOH0FvRjpSut).
2. Move it to the repo root (the folder containing `backend/` and `pipeline/`) and unzip it there:

   ```bash
   cd urban_planner_helper
   unzip -o ~/Downloads/parcel-planner-data-0.1.1.zip
   ```

   This creates:

   ```
   artifacts/person2/                     backend inputs: parcels, places, demographics, review predictions,
                                          backend_manifest.json, source_manifest.json
   artifacts/release.json                 frozen backend release built from that same export
   data/artifacts/release/review_activity_model.joblib   fitted review-activity model (clinic, grocery)
   data/artifacts/release/model_report.json              how the model performed and why it was selected
   ```

   The `.joblib` holds a `parcel_pipeline.model.ReviewActivityModel`. To load it, use the pipeline
   environment (`cd pipeline && uv sync`), then `joblib.load(...)` and call `.predict_log_count(features)`
   with the columns listed in `pipeline/src/parcel_pipeline/features.py`.

3. Run the backend on the real data. For Tract Planner, build the tract index before starting the server; the tract boundaries are included in Git:

   ```bash
   uv venv --python 3.11 .venv && source .venv/bin/activate
   uv pip install -r backend/requirements-release.lock
   python -m backend.build_planning
   PARCEL_PLANNER_MODE=live PARCEL_PLANNER_RELEASE=artifacts/release.json \
     python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
   ```

   Open http://127.0.0.1:8000/docs. Without the two `PARCEL_PLANNER_*` variables, the backend serves synthetic fixture data, which needs no download.

## Current bundle

| Field | Value |
|---|---|
| data_version | `0.1.1+2026-11-15.1+0f1cb84b8325` (model selected: `review-activity/model/0.1.1`) |
| Size | 60 MB zipped, 227 MB unzipped (9 files) |
| SHA-256 | `1f6cff7810b60a45ebf85b7121a4a5a0db3bee8c10c7471d213e8b1ccc20a16e` |

Check the download with `shasum -a 256 parcel-planner-data-0.1.1.zip`.

## Rebuilding the bundle (data owner only)

Rebuilding needs the Bright Data CSVs and a Census API key in `.env`. See [pipeline/README.md](pipeline/README.md).

```bash
cd pipeline && uv run parcel-pipeline all && uv run parcel-pipeline publish && cd ..
python -m backend.build_release --input artifacts/person2 --output artifacts/release.json
zip -X data/bundles/parcel-planner-data-0.1.1.zip artifacts/person2/* artifacts/release.json \
  data/artifacts/release/review_activity_model.joblib data/artifacts/release/model_report.json
```

Then upload the zip to the Drive folder and update the table above.

Do not post the bundle publicly: places, reviews and release.json derive from the hackathon's Bright Data exports.
