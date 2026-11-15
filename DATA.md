# Getting the data

The built data is not in git. It derives partly from the hackathon's Bright Data exports, which should not be in a public repo, and `release.json` is 170 MB. Download the prebuilt bundle instead of rerunning the pipeline.

1. Download `parcel-planner-data.zip` from the team's [DSS FA26 Datathon Drive folder](https://drive.google.com/drive/folders/1iGJ511t7Enpj9ShRZmMOkOH0FvRjpSut).
2. Move it to the repo root (the folder containing `backend/` and `pipeline/`) and unzip it there:

   ```bash
   cd urban_planner_helper
   unzip -o ~/Downloads/parcel-planner-data.zip
   ```

   This creates:

   ```
   artifacts/person2/   backend inputs (parcels, places, demographics, review predictions, manifests)
   artifacts/release.json   frozen backend release built from those inputs
   ```

3. Run the backend on the real data:

   ```bash
   uv venv --python 3.11 .venv && source .venv/bin/activate
   uv pip install -r backend/requirements-release.lock
   PARCEL_PLANNER_MODE=live PARCEL_PLANNER_RELEASE=artifacts/release.json \
     python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
   ```

   Open http://127.0.0.1:8000/docs. Without the two `PARCEL_PLANNER_*` variables, the backend serves synthetic fixture data, which needs no download.

## Current bundle

| Field | Value |
|---|---|
| data_version | `0.1.1+2026-11-15.1+ad464d957df7` |
| Size | 60 MB zipped, 228 MB unzipped |
| SHA-256 | `9abdbba4a727bcb61b6a938fe85a3629485c72272034a0fb553c75c7fa5e4104` |

Check the download with `shasum -a 256 parcel-planner-data.zip`.

## Rebuilding the bundle (data owner only)

Rebuilding needs the Bright Data CSVs and a Census API key in `.env`. See [pipeline/README.md](pipeline/README.md).

```bash
cd pipeline && uv run parcel-pipeline all && uv run parcel-pipeline publish && cd ..
python -m backend.build_release --input artifacts/person2 --output artifacts/release.json
zip -r -X data/bundles/parcel-planner-data.zip artifacts/person2 artifacts/release.json
```

Then upload the zip to the Drive folder and update the table above.

Do not post the bundle publicly: places, reviews and release.json derive from the hackathon's Bright Data exports.
