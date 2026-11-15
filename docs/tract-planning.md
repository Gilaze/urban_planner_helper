# Tract needs and vacant sites

The **Tract Planner** drawer groups source-indicated vacant lots by the supplied SF Census tract boundaries. Choose a tract and one of the existing five services. The dropdown shows up to three physically compatible sites. Pins **1–5** show one suggested location for each service, ordered by that tract's needs; a service without a suitable site gets no pin. Selecting an alternative moves that service's pin. A bold blue boundary distinguishes the selected tract. The main view shows one score and a short reason, with technical information under one **Details** disclosure. The height slider starts at the suggested height and remains editable.

The existing permit checklist is used for the selected site. This feature does not implement permit rules or assign a legal-feasibility score. Its map request timeout is eight seconds (three seconds to connect): observed map requests took three to four seconds and exceeded the previous 2.5-second limit. A map outage still produces an explicit snapshot-zoning fallback in Details.

The suggested-height tick remains fixed while the slider moves. **Show only vacant lots** hides non-vacant buildings in both detailed and distant rendering and from building picking, while keeping the site's concept and numbered, colored arrows visible. Roads and terrain remain as context.

## Local data setup

First install the backend release dependencies and unpack the team's parcel data bundle as described in [DATA.md](../DATA.md). The supplied tract GeoJSON is included at [config/geography/sf-tracts.geojson](../config/geography/sf-tracts.geojson), so teammates do not need a separate boundary download. It is an unchanged copy of the team Drive's `tmph-tgz9.geojson`; see [source details](../config/geography/README.md). It contains 244 tracts, in WGS84 longitude/latitude, with a `data_as_of` value of 2021-02-01. SHA-256: `c613b0b9df678aa095981a988e045c7db070f9e0799113481b3937b0e9591889`.

From the repository root, using the Python environment with `backend/requirements-release.lock` installed:

```bash
python -m backend.build_planning
PARCEL_PLANNER_MODE=live PARCEL_PLANNER_RELEASE=artifacts/release.json \
  python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Then run the simulator with `npm run dev` in `simulator/`. Its default `/api` proxy targets port 8000. Set `PARCEL_PLANNER_API_TARGET` for a different local backend port.

The builder reads existing project snapshots and does not download anything. It writes the ignored `artifacts/planning/index.json`. By default it uses the committed tract boundaries, `artifacts/person2/`, `artifacts/release.json`, and the simulator's cached DataSF footprint pages. Use `--tracts`, `--input`, `--release`, `--footprint-cache`, and `--output` to override these paths. `PARCEL_PLANNER_PLANNING` selects a different index at API startup. Rebuild the index after changing the parcel release or tract input. The parcel ZIP and generated index remain local; a Git pull supplies the boundaries and code, and the build command creates the index for each machine's release.

Building footprints are optional. When all four simulator cache pages are present, their mapped footprints are subtracted from the lots before fitting concepts. When they are absent, site designs explicitly report that existing structures have not been checked. Source-indicated vacancy is a screening classification, not confirmation of present-day availability.

## Calculation and limits

- Each physical lot belongs to the tract covering its representative point. Shared-boundary ties use the lowest GEOID. Lots outside the supplied polygons stay unassigned; they are not moved into a nearby tract. The supplied boundaries assign 3,805 of the corrected bundle's 3,809 vacant candidates. Four remain outside. Population block groups are assigned by their representative points too; all SF population points match this boundary set.
- Tract needs use the existing 800 m 2SFCA model, the frozen category accessibility targets, and population-weighted accessibility deficits. Only supported population points enter the deficit estimate; the UI reports the supported fraction. Block-group populations are not combined with tract totals, and tract medians are not averaged.
- A site's service benefit is `deficit_reduction × covered_population`, scaled to 0–100 relative to the best vacant candidate for that service in the release. This is a weighted access-gap reduction, not a forecast count of new customers. A missing need estimate leaves a site unranked.
- Physical concepts use conservative rectangles contained within the available ground. They respect holes, disconnected land, minimum clear width, and the coverage assumption. They do not assume mapped buildings can be demolished or reused. The five versioned reference programs are in [config/site-fit.json](../config/site-fit.json); these are illustrative assumptions, not legal minimums.
- Fit is 80% reference-program floor-area completion and 20% use of the available clear footprint. Compact programs can fit smaller sites; very large sites do not automatically get giant grocery concepts. Additional floors are suggested only up to the reference program's maximum. Capacity and job/tax estimates are not scaled with these sizes.
- Every tract uses the same scoring rubric. The absolute tract access gap is scaled as `min(100, gap_pct / 30 × 100)`; a 30% gap earns full need points. Final score is **85% tract need + 10% physical fit + 5% population-weighted access improvement**, rounded to the displayed integer before applying thresholds or ordering the options. A zero-gap service can score at most 15, regardless of how well its building fits. Unknown need stays unknown. The 30% reference and weights are explicit prototype assumptions in `config/site-fit.json`, not validated demand forecasts.
- Both the building-type list and its site options use that same score, sorted descending. Thresholds are identical everywhere: **0–24 No clear need; 25–49 May help; 50–74 Needed; 75–100 High need**. A missing compatible site is shown separately from a low-need result. Scores represent independent alternatives; Caden's checklist handles permit review separately.
- Height edits change only the preview. The ranking and one-facility financial estimates continue to use the reference concept.
- The index must match the frozen release's data version and need targets. Stale or missing indexes return an explicit unavailable state while the existing comparison API remains usable.

## API

| Endpoint | Result |
|---|---|
| `GET /api/v1/planning/areas` | Tract polygons, population coverage, five service gaps, candidate counts, source vintages and unassigned counts |
| `GET /api/v1/planning/areas/{geoid}/sites?category=grocery&limit=3` | Ranked compatible sites, size concepts, physical exclusions and missing-need counts |
| `GET /api/v1/planning/areas/{geoid}/recommendations` | The best three options for all five services, used for numbered map pins |
| `GET /api/v1/planning/sites/{parcel_id}` | All five physical concepts for one source-indicated vacant lot |

Run backend checks with `python -m unittest discover -s backend/tests -v`. The real-data browser flow is tract selection → service selection → best-three dropdown → site marker/preview → height slider.

Browser checks use a loaded real-data release and planning index. Run `npm run typecheck` and `npm test` in `tests/integration/browser/`. If using installed Chrome instead of a Playwright-managed browser, set `PLAYWRIGHT_CHANNEL=chrome`. The suite covers 1440×900 and 1280×720, the fixed suggestion tick, the vacant-only filter, drawer state, error states and the WebGL fallback.
