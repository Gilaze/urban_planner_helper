# San Francisco 3D city model (simulator part 1)

A browser 3D model of all of San Francisco: 173k buildings at measured heights, color-coded by building type. The model also includes real terrain and bay bathymetry, OSM streets and bridges, parks, 40k trees, and the Golden Gate Bridge and Sutro Tower. It runs on Three.js with the six-mode camera rig in `src/camera.js`: Default (the Clash-style 3/4 view), isometric, top-down, street, drone, and ride.

## Run it

```bash
cd simulator
npm install
npm run data   # one-time: downloads sources (~200 MB, cached) and writes public/data/ (~14 MB)
npm run dev    # http://localhost:5173
```

`npm run data` needs Python 3.11+ with `numpy`, `Pillow` and `pyarrow`, plus `curl` on PATH. It also needs the Parcel Planner data release from the team Google Drive in `../artifacts/person2/` (see [DATA.md](../DATA.md)). The prebuilt bundle already includes `public/data/`, so with it you can skip `npm run data`. All downloads go through curl, so Python's certificate store is not involved. A rebuild from the cache takes about 2 minutes.

## Controls

| Input | Action |
|---|---|
| Drag / right-drag | Pan (isometric, top-down, Clash) · orbit (street, drone) |
| Wheel | Zoom toward the cursor |
| `Q` / `E` | Rotate the view 90° |
| `W A S D` / arrows | Move across the ground |
| `1`–`5` | Default (3/4 strategy view) · Isometric · Top-down · Street · Drone |
| `V` / legend tab | Colour by building type ↔ by parcel vacancy |
| `T` | Real hills ↔ flat game board |
| `G` | Trees on/off |
| `F` / slider | Focus mode on/off and its clear radius (300 m – 4 km) |
| `O` | Building outlines on/off |
| Legend | Click a type to hide it, hover to highlight it |
| Side drawers | The **Map key** (building type / vacancy legend and controls) docks on the left; the **Parcel Planner** docks on the right. Click a drawer's edge tab to slide it away or bring it back. Each drawer remembers whether it was open. |
| Click a building | Yellow outline and glow, plus a card with its type and vacancy status from `vacancy.bin` (vacant lot / closed businesses / not vacant / unknown). `Esc`, `×` or clicking open ground closes it. The card never shows an address, coordinates or parcel number. |
| Edit a building (in the card) | **Only vacant buildings can be edited**: a vacant lot, or a building where every listed business is closed (Vacancy view, `V`). Other buildings show their info only. The **Type** list only offers types SF rules allow on that lot: no use the zoning district does not permit, and no Tower where the height limit is under 50 m. If nothing fits, the card says so. Picking a type fills in its default height. **Apply** replaces the building on the same footprint if the height is within the SF limit; **Delete** removes it. **Restore original** undoes one building; **Reset all** (bottom left) undoes every edit. Edits are a local sketch saved in this browser only. |

In the browser console, `__sf.jump(lon, lat, viewHeightMeters, mode)` moves the camera.

## Vacancy view

Press `V`, or use the legend's **Vacancy** tab, to colour the city by vacancy instead of building type. Vacant lots without a building are hatched red on the ground.

The data comes from the team's Parcel Planner data release (the shared Google Drive folder `parcel-planner-data`). `npm run data` reads it from `../artifacts/person2/` (or `../data/artifacts/person2/`); set `PARCEL_PLANNER_DATA` to read it from somewhere else.

| Colour | Class | Rule |
|---|---|---|
| Red | Vacant lot | `parcels.geoparquet` `vacancy_status = source_indicated_vacant`: coded VACANT in 2023 and no recorded use now. |
| Orange | Closed businesses | Every Google Maps business matched to the building is closed, permanently or temporarily (`places.parquet` `closure_status`). A place is matched to the footprint containing it, or to the nearest wall within 6 m. |
| Pale blue | Occupied | `vacancy_status = not_vacant`. |
| Grey | Unknown | `vacancy_status = unknown`, or no parcel match. |

Current counts:

| Class | Buildings | Lots |
|---|---|---|
| Vacant lot | 853 | 4,581 |
| Closed businesses | 510 | – |
| Occupied | 165,589 | 148,285 |
| Unknown | 6,486 | 2,161 |

21,773 of the 24,909 SF Google Maps places matched a building.

`npm run data` writes `public/data/vacancy.bin` (one byte per building, in `buildings.bin` order) and `public/data/vacant.png` (a mask of the vacant lots).

## Focus mode (clear radius)

Focus mode keeps full detail inside a circle around the view centre. In street mode the circle is centred on the camera. Inside it you get facade windows, roof detail, shadows and trees. Outside it, each building switches to a low-detail stand-in: a simplified footprint prism with a flat-coloured, unlit-detail shader and no shadows. A post-process pass also blurs and slightly hazes everything beyond the radius, with a soft edge.

- The switch happens per building in the vertex shader, using each building's centroid, so the edge is an exact circle and moves with you.
- Whole 1 km chunks that sit entirely inside or entirely outside the circle are culled on the CPU.
- The two mesh sets are generated in parallel web workers.
- The sun's shadow map only covers the clear radius, so shadows are sharper and cheaper.

Measured on an RTX 4060 Laptop GPU at 1600×900 with the frame cap removed:

| View | Focus on | Focus off |
|---|---|---|
| Clash 3/4, 3 km view | 227 fps | 110 fps |
| Whole city | 122 fps | 70 fps |

**Laptops with two GPUs:** browsers default to the integrated GPU. In Windows, go to Settings › Display › Graphics, add `chrome.exe` and set it to *High performance*. The title bar shows which GPU is rendering.

## Outlines

The post-process pass also draws outlines. It reads the depth buffer and finds pixels where the surface stops being one flat plane across a 3×3 neighbourhood: silhouettes against the ground or sky, and wall/roof creases. It darkens those pixels to about 30% of their own colour, so the lines read as cartoon-style ink rather than black.

- The test compares the second difference to the first difference, so it doesn't depend on scale and works the same in orthographic and perspective views.
- Lines are about 0.8 px wide (1–2 device pixels).
- They fade out once a pixel covers more than about 3 m, so the zoomed-out city doesn't turn to mush.
- They are softer inside the blurred zone.

## Water

The bay's colour comes from depth in the elevation data, with animated surf along the shore. Its sun glitter comes from a sparse grid of wave facets that flash when they mirror the sun toward the camera, re-rolling a few times a second.

- Glints never get smaller than about 1.5 px, so they stay crisp instead of shimmering.
- Water is mostly exempt from the focus blur.

## Building rules (San Francisco)

When you replace a building, the card checks two San Francisco rules for the lot it stands on (`src/buildingRules.js`):

1. **Height limit**: from the Planning Code's [Zoning Map – Height and Bulk Districts](https://data.sfgov.org/d/h9wh-cg3m) (e.g. `40-X` = 40 ft). Taller than the limit blocks Apply. Bulk controls (the letter) are not modelled. Open space (`OS`) and districts whose height is set by a special plan (Candlestick Point, Hunters Point, Special Height Districts) have no numeric limit.
2. **Use**: whether the use is allowed in the lot's zoning district (`zoning_code` from DataSF parcels). This is a **simplified** version of the Planning Code zoning control tables (Articles 2, 7 and 8), grouped by district family. **P** = permitted, **C** = needs Conditional Use authorization (allowed with a note), **N** = not permitted (blocks Apply). Special plan and redevelopment areas (Mission Bay, Hunters Point, Parkmerced, …) are flagged "check the plan". Individual districts have exceptions; check the code for a real proposal.

| Family (districts) | Hospital | Clinic | Pharmacy | Grocery | Bank | School | Tower | Office | Apartment | Flats | House | Mixed | Retail | Hotel | Civic | Medical | Industrial |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RH (RH-1, RH-2, RH-3) | C | N | N | N | N | C | N | N | N | P | P | N | N | N | C | N | N |
| RM / RTO | C | C | N | N | N | C | N | N | P | P | P | N | N | N | C | C | N |
| RC (RC-3, RC-4, RTO-C) | C | P | P | P | P | C | N | C | P | P | P | P | P | C | C | P | N |
| NC (NC-1/2/3/S, NCD, NCT, …) | C | P | P | P | C | C | N | P | P | P | P | P | P | C | C | P | N |
| C-2 | C | P | P | P | P | C | N | P | P | P | P | P | P | P | P | P | C |
| C-3 (downtown) | C | P | P | P | P | C | P | P | P | P | P | P | P | P | P | P | N |
| PDR (PDR-*, M-1, M-2, SALI) | N | N | P | P | C | N | N | N | N | N | N | N | P | N | C | N | P |
| Eastern Neighborhoods mixed use (UMU, MUG, MUR, MUO, …) | C | P | P | P | P | C | N | C | P | P | P | P | P | C | P | P | P |
| P (public) | P | P | N | N | N | P | N | N | N | N | N | N | N | N | P | P | N |

Sketch conventions, not SF rules: picking a type fills in a typical height, capped at the lot's limit. Heights start at 3 m. A Tower is 50 m or taller, the map's own definition. Lot zoning and height come from the Parcel Planner release (`parcels.geoparquet`: `zoning_code`, `height_limit_ft`) and are written per building to `public/data/zoning.bin`.

## Data and accuracy

| Layer | Source | Notes |
|---|---|---|
| Footprints and heights | [DataSF Building Footprints](https://data.sfgov.org/d/ynuv-fyni) | LiDAR median roof height per footprint. San Mateo County footprints (`SM*`) are dropped. |
| Building type | Parcel Planner release `parcels.geoparquet` (team Drive): `landuse_2023` and `residential_units_2023`, falling back to `current_uses` | Joined on map-block-lot. Anything 50 m or taller is classed as a **Tower**, whatever its use. |
| Newer towers | OpenStreetMap `height` / `building:levels` | The LiDAR flight is from 2010. Footprints whose LiDAR *peak* is under 60% of the OSM height take the OSM height (47 buildings, e.g. Salesforce Tower at 326 m and 181 Fremont). Tall OSM buildings with no footprint are added (124). |
| Roof shapes | LiDAR max/mean vs. median | Hipped roofs (13k small rectangular buildings), stepped podium+tower (96), tapered (Transamerica). |
| Streets, bridges, parks, beaches, lakes, piers | OpenStreetMap (Overpass) | Bridge decks are interpolated between their ground anchors. The Golden Gate deck is fixed at 67 m. |
| Terrain | AWS Terrain Tiles (Terrarium z14) | 20 m grid. Includes bay bathymetry. |

Google Maps is not used: its terms do not allow extracting building geometry. The DataSF LiDAR footprints are more accurate for this purpose anyway.

Building type mapping (`classify()` in `pipeline/build_data.py`):

| Type | Rule |
|---|---|
| Tower / high-rise | Height ≥ 50 m |
| Apartment / condo | Residential, 5 or more units |
| Flats | Residential, 2–4 units |
| House | Residential, 0–1 units |
| Mixed-use | MIXRES |
| Retail / commercial | RETAIL/ENT, MIXED |
| Office | MIPS |
| Hotel | VISITOR |
| Civic | CIE |
| Medical | MED |
| Industrial | PDR |
| Other | Vacant / open-space parcels, no parcel match, or sheds under 20 m² |
| Pier | OSM `man_made=pier` |

## How it is built

- `pipeline/build_data.py` projects everything into a local metric frame. The origin is −122.435, 37.770, with +x east, +z south and +y up. It writes compact little-endian binaries to `public/data/`; the record layouts are in the docstrings. Buildings keep the input order, so a building's index is stable between runs of the same data.
- `src/geometryWorker.js` runs in a web worker and turns the binaries into 1 km mesh chunks. Each wall facade gets its own quad so windows can be centered per facade. Roofs are triangulated with earcut. Roads are stacked sidewalk and asphalt ribbons, and bridges get fascias and pillars.
- `src/materials.js` holds the procedural shaders. All surface detail is computed in the fragment shader rather than read from bitmap textures, so it stays sharp at any zoom:
  - Buildings: per-type window grids, storefronts, cornices, corner shading and roof seams/shingles.
  - Roads: lane markings and sidewalk joints.
  - Ground: mowing stripes, crisp park and beach edges.
  - Water: depth color and animated shoreline foam.

  Patterns fade to their average color once they would be smaller than a pixel, which avoids moiré. Every material shares one uniform set, so the hills/flat toggle, type hiding and highlighting are instant.
- `src/main.js` handles lighting and the shadow frustum, which follows the view and snaps to texels. It also adapts render resolution to frame time and skips the shadow pass when zoomed out to the whole city.
- `src/camera.js` is the supplied camera controller. Its angles, FOVs and tweens are unchanged; distances are multiplied by `worldScale` (5) and the zoom-out ceiling is raised so the whole city fits.

## Known limitations

- Heights are medians, so pitched and irregular roofs are approximations. Buildings built after 2010 that have no OSM height still show their 2010 state.
- Terrain is a 20 m grid. Buildings sit at the mean ground height of their footprint, and walls extend down to the lowest corner.
- Tunnels are omitted, and freeway viaduct heights are estimated.
