# Shared demo data

The original complete team Drive ZIP is included unchanged at the user's explicit request so a Git pull supplies the demo data. Its SHA-256 is `b94efdcc5603f4710e34079eb62212b30defbbcaa5b62bccf84fa93deff1fdbc`.

Source: [parcel-planner-data-0.1.1.zip in the team Drive](https://drive.google.com/file/d/1IHeW1k6QcVnFfSQcCTNXHeozQrVIeZJo/view).

`planning-index.json.gz` contains the matching tract index previously built from that ZIP's canonical inputs and the supplied tract boundaries, with cached project building footprints excluded from proposed open ground. It covers 244 tracts and 3,809 source-indicated vacant lots (four outside the supplied tract polygons remain unassigned). `manifest.json` records checksums for the ZIP, serving release, tract index, and tract boundaries.

The backend verifies checksums and unpacks the serving release and index into a versioned temporary directory automatically. Raw tables and model-processing files remain available inside the original ZIP; they are not needed to start the demo. The API defaults to live bundled data. Explicit `PARCEL_PLANNER_MODE=fixture` still selects synthetic fixtures for development; explicit release/index paths override the bundle.

The backend Vercel project uses the repository root and `index.py`; the frontend project uses `simulator/` and its existing API rewrite. The ZIP and index are server files, outside the frontend public directory. Python release-building dependencies are not needed at runtime.

For normal local startup, install `backend/requirements.lock`, run `python -m uvicorn backend.api:app --port 8000`, then run `npm run dev` in `simulator/`. No separate data download, extraction, or tract-index build is required after pulling this commit. Restart the backend after pulling new code.
