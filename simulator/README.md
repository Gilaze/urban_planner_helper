# San Francisco 3D city model (simulator part 1)

A browser 3D model of all of San Francisco: 173k buildings at measured heights, color-coded by building type. The model also includes real terrain and bay bathymetry, OSM streets and bridges, parks, 40k trees, and the Golden Gate Bridge and Sutro Tower. It runs on Three.js with the six-mode camera rig in `src/camera.js`: Default (the Clash-style 3/4 view), isometric, top-down, street, drone, and ride.

## Run it

```bash
cd simulator
npm install
npm run data   # one-time: downloads sources (~200 MB, cached) and writes public/data/ (~14 MB)
npm run dev    # http://localhost:5173
```

`npm run data` needs Python 3.11+ with `numpy` and `Pillow`, plus `curl` on PATH. All downloads go through curl, so Python's certificate store is not involved. A rebuild from the cache takes about 2 minutes.

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

In the browser console, `__sf.jump(lon, lat, viewHeightMeters, mode)` moves the camera.

## Vacancy view

Press `V`, or use the legend's **Vacancy** tab, to colour the city by parcel vacancy instead of building type. Buildings on vacant lots turn red, occupied ones pale blue, and unknown ones grey. Vacant lots without a building are hatched red on the ground.

The rule is the same one the Parcel Planner pipeline uses (`pipeline/src/parcel_pipeline/sources/sf.py`):

| Status | Rule |
|---|---|
| Vacant | The lot is coded `VACANT` in the 2023 land-use survey (`fdfd-xptc`) and has no recorded use in the current land-use parcel data (`c5ge-t6pj`). |
| Occupied | The current data records any use: residential units, any commercial floor area, parking, or open space. Lots with a 2023 use that are absent from the current data also count. |
| Unknown | Everything else, including buildings with no parcel match. |

Current counts:

| Status | Lots | Buildings |
|---|---|---|
| Vacant | 4,591 | 856 |
| Occupied | 148,478 | 166,425 |
| Unknown | 1,770 | 6,157 |

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

## Data and accuracy

| Layer | Source | Notes |
|---|---|---|
| Footprints and heights | [DataSF Building Footprints](https://data.sfgov.org/d/ynuv-fyni) | LiDAR median roof height per footprint. San Mateo County footprints (`SM*`) are dropped. |
| Building type | [SF Land Use 2023](https://data.sfgov.org/d/fdfd-xptc) | Joined on map-block-lot (97% of footprints match). Anything 50 m or taller is classed as a **Tower**, whatever its use. |
| Newer towers | OpenStreetMap `height` / `building:levels` | The LiDAR flight is from 2010. Footprints whose LiDAR *peak* is under 60% of the OSM height take the OSM height (47 buildings, e.g. Salesforce Tower at 326 m and 181 Fremont). Tall OSM buildings with no footprint are added (124). |
| Roof shapes | LiDAR max/mean vs. median | Hipped roofs (13k small rectangular buildings), stepped podium+tower (96), tapered (Transamerica). |
| Streets, bridges, parks, beaches, lakes, piers | OpenStreetMap (Overpass) | Bridge decks are interpolated between their ground anchors. The Golden Gate deck is fixed at 67 m. |
| Terrain | AWS Terrain Tiles (Terrarium z14) | 20 m grid. Includes bay bathymetry. |

Google Maps is not used: its terms do not allow extracting building geometry. The DataSF LiDAR footprints are more accurate for this purpose anyway.

Building type mapping (`classify()` in `pipeline/build_data.py`):

| Type | Rule |
|---|---|
| Tower / high-rise | Height ≥ 50 m |
| Apartment / condo | Residential APTS / CONDO / SRO, or ≥ 5 units |
| Flats | Residential FLATS, or 2–4 units |
| House | Residential, single unit |
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
