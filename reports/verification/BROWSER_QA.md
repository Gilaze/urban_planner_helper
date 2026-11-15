# Browser QA of the current repository

Baseline: `35f3947`. The flow under test is: the existing localhost app loads →
parcel selection → proposal preview → reweight → reset, including the planned
stale/unavailable/geometry/basemap states. No Frontend product changes were made.

## Environment and method

- App: `http://127.0.0.1:5173/`, the committed `simulator/` city viewer.
- API: `http://127.0.0.1:8765/api/v1`, using the repository's fixture inputs.
- Viewports: 1440×900 and 1280×720.
- Playwright 1.65.1 with installed Google Chrome 157.0.8103.78. Browser plugin
  not available; regular Playwright was used. No browser binary was downloaded.
- Temporary configuration imported the repository's existing Playwright config,
  selected `channel: 'chrome'`, and enabled local software WebGL with
  `--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader`.
- Inputs: committed repository files only. Generated city binaries are absent;
  no external source-data fetch was permitted for this audit.

## Findings

**P4-FRONTEND-001:** the planned planner flow is not present in the existing city
viewer. All **16 tests fail**, with no skips or flaky passes: 14 fail waiting for
the missing parcel selector and two late-comparison tests time out before a
comparison request. Reweighting, reset and the downstream error-state assertions
cannot be reached. This is an entry-point gap, not 16 separate demonstrated bugs.
Static inspection of `simulator/src/main.js`, `data.js` and `ui.js` also finds city
binary loading and camera controls, rather than planner API calls or comparison
controls. Owner: Frontend / Person 1.

**P4-FRONTEND-002:** with only tracked files, the simulator cannot load its city.
`/data/meta.json` returns HTTP 200 with `text/html` from Vite's fallback. JSON
parsing throws `Unexpected token '<'`, and the UI remains at “Could not load the
city data.” The README's `npm run data` downloads sources; it was not run to
completion under the final repository-only scope. This is a missing-input
readiness gap for this audit. It does not evaluate the complete city build with
all required assets. Owner: Frontend / Person 1, with Data for the asset handoff.

| Check, both sizes | Result |
| --- | --- |
| Page URL/title | PASS — San Francisco 3D City Model |
| Nonblank screen | PASS — loading/error UI is visible |
| Framework error overlay | PASS — no Vite overlay |
| Horizontal overflow | PASS — none observed |
| Console health | FAIL — city metadata JSON parse error |
| Screenshot review | Captured and visually inspected; same loader failure at both sizes |
| Planner interaction proof | FAIL — no parcel selector/comparison controls |
| Integrated normal/error flows | 0 passed, 16 failed, 0 skipped |

No interaction was declared working from a build alone. `npm ci && npm run build`
passes, with a large-bundle warning; it does not supply ignored city data or prove
an integrated planner experience. Mobile, other browsers and a rendered full-city
scene were not checked.

## Evidence and reproduction

[browser-results.json](current/browser-results.json) retains every native
Playwright test/project/status/duration and an error summary. Repeated source
excerpts, ANSI formatting, call logs and local trace attachments are omitted;
the exact raw report SHA256 is retained. [browser-smoke.json](current/browser-smoke.json)
records page text, console/HTTP evidence, controls, source fingerprints and local
screenshot paths/hashes. Screenshots stay outside the repository under
`/private/tmp/parcel-p4-repository-only/`.

From the repo root, start the fixture API and simulator in separate terminals:

```sh
PARCEL_PLANNER_MODE=fixture backend/.venv/bin/python -m uvicorn backend.api:app \
  --host 127.0.0.1 --port 8765
cd simulator
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Run `tests/integration/browser`'s configured suite with `APP_BASE_URL` and
`API_BASE_URL` set to the URLs above. The repository config defaults to bundled
Chromium; this run used a temporary config with the installed-Chrome launch
settings listed above and an output directory outside the repo. Its exact local
command was:

```sh
APP_BASE_URL=http://127.0.0.1:5173 API_BASE_URL=http://127.0.0.1:8765/api/v1 \
  npm exec playwright test -- \
  --config /private/tmp/parcel-p4-repository-only/playwright.config.mjs
```

The local screenshots are `simulator-1440x900.png` and `simulator-1280x720.png` in
that same temporary directory. [manual-evidence.json](current/manual-evidence.json)
explicitly leaves unobservable UI and real-data checks `NOT_RUN`. Release and
presentation acceptance remain false.
