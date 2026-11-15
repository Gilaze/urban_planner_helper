#!/usr/bin/env python3
"""Build the compact binary data files used by the San Francisco 3D simulator.

Sources (downloaded with curl into pipeline/cache/, then reused):
  * DataSF Building Footprints (ynuv-fyni)  - 177k footprints with LiDAR-measured heights
  * Parcel Planner data release (team Google Drive, data/artifacts/person2):
      parcels.geoparquet - per-lot land use (2023 + current), residential units, vacancy_status, lot geometry
      places.parquet     - Google Maps Places (Bright Data) with closure_status
  * OpenStreetMap via Overpass - streets, bridges, parks, forests, beaches, lakes, piers
  * AWS Terrain Tiles (Terrarium encoding, zoom 14) - ground elevation + bay bathymetry

Outputs (public/data/):
  meta.json, terrain.bin, buildings.bin, roads.bin, lakes.bin, trees.bin,
  landcover.png (R=grass/park, G=forest/scrub, B=sand), urban.png (built-up mask)

Usage:  python pipeline/build_data.py            (needs: numpy, Pillow, curl on PATH)
"""
import glob
import json
import math
import os
import struct
import subprocess
import sys
from collections import Counter, defaultdict

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.normpath(os.path.join(HERE, "..", "public", "data"))
os.makedirs(CACHE, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------------------
# Local projection: metres on a tangent plane. +x = east, +z = south, +y = up.
# Over a 19 km window the equirectangular error is well under 0.1%.
# ---------------------------------------------------------------------------
LON0, LAT0 = -122.4350, 37.7700
_PHI = math.radians(LAT0)
M_LAT = 111132.954 - 559.822 * math.cos(2 * _PHI) + 1.175 * math.cos(4 * _PHI)
M_LON = 111412.84 * math.cos(_PHI) - 93.5 * math.cos(3 * _PHI)

EXT = 9600.0          # world spans [-EXT, EXT] metres on both axes
GRID = 20.0           # terrain sample spacing (m)
N = int(round(2 * EXT / GRID)) + 1
Q = 0.25              # footprint / road coordinate quantum (m) -> int16 covers +-8.1 km
LC_RES = 4096         # landcover texture resolution (4.7 m / px)
UA = "sf-sim-pipeline/0.1"


def proj(lon, lat):
    return (np.asarray(lon) - LON0) * M_LON, -(np.asarray(lat) - LAT0) * M_LAT


def unproj(x, z):
    return LON0 + np.asarray(x) / M_LON, LAT0 - np.asarray(z) / M_LAT


def fetch(url, path, data_file=None):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    cmd = ["curl", "-sSfL", "--compressed", "-A", UA, "-o", path + ".part"]
    if data_file:
        cmd += ["--data-urlencode", "data@" + data_file]
    subprocess.run(cmd + [url], check=True)
    os.replace(path + ".part", path)
    return path


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# 1. Terrain
# ---------------------------------------------------------------------------
def tile_xy(lon, lat, z):
    n = 2.0 ** z
    lat_r = np.radians(lat)
    return (np.asarray(lon) + 180.0) / 360.0 * n, (1.0 - np.arcsinh(np.tan(lat_r)) / np.pi) / 2.0 * n


def build_terrain():
    z = 14
    lon_w, lat_n = unproj(-EXT - 300, -EXT - 300)
    lon_e, lat_s = unproj(EXT + 300, EXT + 300)
    fx0, fy0 = tile_xy(lon_w, lat_n, z)
    fx1, fy1 = tile_xy(lon_e, lat_s, z)
    tx0, ty0, tx1, ty1 = int(fx0), int(fy0), int(fx1), int(fy1)
    tdir = os.path.join(CACHE, "terrarium")
    os.makedirs(tdir, exist_ok=True)
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            p = fetch(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{tx}/{ty}.png",
                      os.path.join(tdir, f"{z}_{tx}_{ty}.png"))
            rgb = np.asarray(Image.open(p).convert("RGB"), np.float32)
            h = rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = h
    log(f"  terrain tiles: {(tx1 - tx0 + 1) * (ty1 - ty0 + 1)}")
    xs = -EXT + np.arange(N) * GRID
    X, Z = np.meshgrid(xs, xs)               # row = z index, col = x index
    lon, lat = unproj(X, Z)
    px, py = tile_xy(lon, lat, z)
    u = (px - tx0) * 256.0 - 0.5
    v = (py - ty0) * 256.0 - 0.5
    H = bilinear(mosaic, u, v)
    return H.astype(np.float32)


def bilinear(img, u, v):
    h, w = img.shape
    u = np.clip(u, 0, w - 1.001)
    v = np.clip(v, 0, h - 1.001)
    i0 = np.floor(u).astype(np.int64)
    j0 = np.floor(v).astype(np.int64)
    fu = u - i0
    fv = v - j0
    a = img[j0, i0] * (1 - fu) + img[j0, i0 + 1] * fu
    b = img[j0 + 1, i0] * (1 - fu) + img[j0 + 1, i0 + 1] * fu
    return a * (1 - fv) + b * fv


def hsample(H, x, z):
    return bilinear(H, (np.asarray(x, np.float64) + EXT) / GRID, (np.asarray(z, np.float64) + EXT) / GRID)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def signed_area(xs, zs):
    return 0.5 * float(np.sum(xs * np.roll(zs, -1) - np.roll(xs, -1) * zs))


def rdp(pts, eps):
    """Ramer-Douglas-Peucker on an open polyline (Nx2 array)."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        d = q - p
        seg = pts[a + 1:b] - p
        L = math.hypot(d[0], d[1])
        if L < 1e-9:
            dist = np.hypot(seg[:, 0], seg[:, 1])
        else:
            dist = np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / L
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = a + 1 + k
            keep[m] = True
            stack += [(a, m), (m, b)]
    return pts[keep]


def clean_ring(xs, zs, eps=0.35):
    pts = np.stack([xs, zs], 1)
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    # simplify as a closed ring: split at the point farthest from vertex 0
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
    a = rdp(np.vstack([pts[:far + 1]]), eps)
    b = rdp(np.vstack([pts[far:], pts[:1]]), eps)
    ring = np.vstack([a[:-1], b[:-1]])
    # drop consecutive duplicates after quantisation
    q = np.round(ring / Q).astype(np.int32)
    keep = np.ones(len(q), bool)
    keep[1:] = np.any(q[1:] != q[:-1], axis=1)
    if len(q) > 1 and np.all(q[-1] == q[0]):
        keep[-1] = False
    q = q[keep]
    if len(q) < 3:
        return None
    return q


def stitch(segments):
    """Join open way geometries into closed rings by matching endpoints."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        cur = segs.pop()
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += s[::-1][1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[::-1][:-1] + cur
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def element_polygons(e):
    """Return list of (outer_ring, [inner_rings]) in lon/lat for a closed way / multipolygon."""
    if e["type"] == "way":
        g = [(p["lon"], p["lat"]) for p in e.get("geometry", []) if p]
        if len(g) >= 4 and g[0] == g[-1]:
            return [(g, [])]
        return []
    outers, inners = [], []
    for m in e.get("members", []):
        if m["type"] != "way" or not m.get("geometry"):
            continue
        g = [(p["lon"], p["lat"]) for p in m["geometry"] if p]
        (inners if m.get("role") == "inner" else outers).append(g)
    outer_rings = stitch(outers)
    inner_rings = stitch(inners)
    if not outer_rings:
        return []
    # Attach every inner ring to the first outer ring (good enough for rasterising).
    return [(outer_rings[0], inner_rings)] + [(r, []) for r in outer_rings[1:]]


def orient_rings(poly):
    """Project, simplify, quantise and orient one polygon (list of lon/lat rings)."""
    rings = []
    for k, ring in enumerate(poly):
        xs, zs = proj([c[0] for c in ring], [c[1] for c in ring])
        q = clean_ring(np.asarray(xs), np.asarray(zs))
        if q is None:
            if k == 0:
                return []
            continue
        if np.abs(q).max() > 32000:
            return []
        a = signed_area(q[:, 0].astype(np.float64), q[:, 1].astype(np.float64)) * Q * Q
        if k == 0 and abs(a) < 4.0:
            return []
        # Outer rings clockwise in (x,z) (negative area), holes counter-clockwise.
        if (k == 0 and a > 0) or (k > 0 and a < 0):
            q = q[::-1]
        rings.append(q)
    return rings


# Structures drawn by hand in the client (src/world.js). Their LiDAR footprints would be solid blocks.
SUTRO = (-122.45281, 37.75518)
GG_S, GG_N = (-122.47785, 37.81402), (-122.47923, 37.82551)


def landmark_footprint(x, z, peak):
    sx, sz = proj(*SUTRO)
    if math.hypot(x - sx, z - sz) < 70 and peak > 40:
        return True
    ax, az = proj(*GG_S)
    bx, bz = proj(*GG_N)
    dx, dz = bx - ax, bz - az
    L2 = dx * dx + dz * dz
    t = ((x - ax) * dx + (z - az) * dz) / L2
    if -0.45 < t < 1.45:  # main span plus side spans
        px, pz = ax + t * dx, az + t * dz
        if math.hypot(x - px, z - pz) < 45 and peak > 25:
            return True
    return False


def point_in_poly(x, z, xs, zs):
    inside = False
    j = len(xs) - 1
    for i in range(len(xs)):
        if (zs[i] > z) != (zs[j] > z) and x < (xs[j] - xs[i]) * (z - zs[i]) / (zs[j] - zs[i] + 1e-12) + xs[i]:
            inside = not inside
        j = i
    return inside


def parse_height(t):
    for key in ("height", "building:height"):
        v = t.get(key)
        if v:
            try:
                return float(v.replace("m", "").split()[0].split(";")[0])
            except ValueError:
                pass
    try:
        return float(t.get("building:levels", "").split(";")[0]) * 3.4
    except ValueError:
        return 0.0


def osm_type(tag, h):
    if h >= TOWER_M:
        return TID["tower"]
    return TID[{"apartments": "apartment", "residential": "apartment", "condominium": "apartment",
                "dormitory": "apartment", "house": "house", "office": "office", "commercial": "office",
                "hotel": "hotel", "retail": "retail", "supermarket": "retail", "hospital": "medical",
                "university": "civic", "school": "civic", "college": "civic", "civic": "civic",
                "public": "civic", "government": "civic", "church": "civic", "industrial": "industrial",
                "warehouse": "industrial"}.get(tag, "other")]


def load_osm_tall():
    path = os.path.join(CACHE, "osm_buildings.json")
    fetch_overpass(os.path.join(HERE, "overpass_buildings.ql"), path)
    with open(path, encoding="utf-8") as f:
        els = json.load(f)["elements"]
    res = []
    for e in els:
        t = e.get("tags", {})
        h = parse_height(t)
        if h < 18:
            continue
        for outer, _inners in element_polygons(e):
            lon = np.array([p[0] for p in outer])
            lat = np.array([p[1] for p in outer])
            if lat.mean() > 37.807 and lon.mean() < -122.47:
                continue  # Golden Gate Bridge parts - drawn as a landmark instead
            xs, zs = proj(lon, lat)
            res.append(dict(x=xs, z=zs, x0=xs.min(), x1=xs.max(), z0=zs.min(), z1=zs.max(), h=h,
                            area=max(1.0, abs(signed_area(xs, zs))), tag=t.get("building", "yes"),
                            name=t.get("name", "")))
    return res


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]


def fetch_overpass(ql, path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            if f.read(1) == b"{":
                return path
    for url in OVERPASS:
        try:
            if os.path.exists(path):
                os.remove(path)
            fetch(url, path, data_file=ql)
            with open(path, "rb") as f:
                if f.read(1) == b"{":
                    return path
        except subprocess.CalledProcessError:
            pass
    raise RuntimeError("all Overpass endpoints failed for " + ql)


# ---------------------------------------------------------------------------
# 2. Building classification
# ---------------------------------------------------------------------------
TYPES = [
    ("tower", "Tower / high-rise", "#86A9F7"),
    ("office", "Office", "#67D2DE"),
    ("apartment", "Apartment / condo", "#FFAA70"),
    ("flats", "Flats (2-4 units)", "#FFD56E"),
    ("house", "Single-family house", "#FFF0A8"),
    ("mixed", "Mixed-use (homes over shops)", "#CCA8F4"),
    ("retail", "Retail / commercial", "#FF8E8E"),
    ("hotel", "Hotel", "#F9A6D6"),
    ("civic", "Civic / school / culture", "#8DDD96"),
    ("medical", "Medical", "#D4F1F6"),
    ("industrial", "Industrial / PDR", "#CDB79F"),
    ("other", "Other / accessory / unknown", "#E4E1DB"),
    ("pier", "Pier / wharf", "#D9C6A5"),
]
TID = {t[0]: i for i, t in enumerate(TYPES)}
TOWER_M = 50.0


def classify(lu, height, area):
    if area < 20.0 and height < 6.0:
        return TID["other"]
    if height >= TOWER_M:
        return TID["tower"]
    if lu is None:
        return TID["other"]
    use = (lu.get("landuse") or "").upper()
    restype = (lu.get("restype") or "").upper()
    try:
        units = int(float(lu.get("resunits") or 0))
    except ValueError:
        units = 0
    if use == "RESIDENT":
        if restype in ("APTS", "CONDO", "SRO", "LIVEWORK") or units >= 5:
            return TID["apartment"]
        if restype == "FLATS" or 2 <= units <= 4:
            return TID["flats"]
        return TID["house"]
    return TID[{
        "MIXRES": "mixed", "MIXED": "retail", "RETAIL/ENT": "retail", "MIPS": "office",
        "CIE": "civic", "MED": "medical", "VISITOR": "hotel", "PDR": "industrial",
    }.get(use, "other")]


# ---------------------------------------------------------------------------
# 3. Downloads
# ---------------------------------------------------------------------------
# Building vacancy codes written to vacancy.bin
VAC_UNKNOWN, VAC_OCCUPIED, VAC_VACANT_LOT, VAC_CLOSED = 0, 1, 2, 3
TEAM_STATUS = {"source_indicated_vacant": VAC_VACANT_LOT, "not_vacant": VAC_OCCUPIED, "unknown": VAC_UNKNOWN}
# Parcel Planner data release (the team's shared Google Drive folder, "parcel-planner-data").
# The data bundle unzips to <repo>/artifacts/person2; older notes used <repo>/data/artifacts/person2.
_TEAM_DATA_CANDIDATES = [os.path.normpath(os.path.join(HERE, "..", "..", *p))
                         for p in (("artifacts", "person2"), ("data", "artifacts", "person2"))]
TEAM_DATA = os.environ.get("PARCEL_PLANNER_DATA") or next(
    (d for d in _TEAM_DATA_CANDIDATES if os.path.exists(os.path.join(d, "parcels.geoparquet"))), _TEAM_DATA_CANDIDATES[0])
CURRENT_USE_TYPE = {"retail": "RETAIL/ENT", "mips": "MIPS", "cie": "CIE", "med": "MED", "pdr": "PDR", "visitor": "VISITOR"}


def wkb_polygons(b):
    """Minimal WKB reader for (Multi)Polygon -> list of polygons, each a list of (N,2) lon/lat rings."""
    def read(off):
        e = "<" if b[off] == 1 else ">"
        (t,) = struct.unpack_from(e + "I", b, off + 1)
        off += 5
        t %= 1000
        if t == 3:
            (nr,) = struct.unpack_from(e + "I", b, off)
            off += 4
            rings = []
            for _ in range(nr):
                (n,) = struct.unpack_from(e + "I", b, off)
                off += 4
                rings.append(np.frombuffer(b, dtype=e + "f8", count=2 * n, offset=off).reshape(n, 2))
                off += 16 * n
            return [rings], off
        if t == 6:
            (k,) = struct.unpack_from(e + "I", b, off)
            off += 4
            polys = []
            for _ in range(k):
                ps, off = read(off)
                polys += ps
            return polys, off
        raise ValueError(f"unsupported WKB geometry type {t}")
    return read(0)[0]


def load_team_release():
    """Parcels and Places from the Parcel Planner data release (team Google Drive).

    parcels.geoparquet : per physical lot - vacancy_status (team rule), landuse_2023,
                         residential_units_2023, current_uses, WGS84 geometry
    places.parquet     : Google Maps Places (Bright Data export) with closure_status
    """
    import pyarrow.parquet as pq
    pp = os.path.join(TEAM_DATA, "parcels.geoparquet")
    pl = os.path.join(TEAM_DATA, "places.parquet")
    for f in (pp, pl):
        if not os.path.exists(f):
            raise SystemExit(f"Missing {f}. Put the Parcel Planner release (Drive: parcel-planner-data/artifacts/person2) "
                             f"there (unzip parcel-planner-data.zip at the repo root, see DATA.md) or set PARCEL_PLANNER_DATA.")
    have = set(pq.read_schema(pp).names)
    rule_cols = [c for c in ("zoning_code", "height_limit_ft", "height_special_plan", "height_open_space") if c in have]
    t = pq.read_table(pp, columns=["mapblklot", "vacancy_status", "landuse_2023", "residential_units_2023",
                                   "current_uses", "current_residential_units", "geometry", *rule_cols]).to_pylist()
    landuse, status, geoms, lot_rules = {}, {}, {}, {}
    for r in t:
        lot = r["mapblklot"]
        code = r["landuse_2023"]
        units = r["residential_units_2023"]
        if not code and r["current_uses"]:
            uses = r["current_uses"].split(",")
            if "residential" in uses:
                code = "MIXRES" if any(u in CURRENT_USE_TYPE for u in uses) else "RESIDENT"
                units = r["current_residential_units"]
            else:
                code = next((CURRENT_USE_TYPE[u] for u in uses if u in CURRENT_USE_TYPE), None)
        if code:
            landuse[lot] = {"landuse": code, "resunits": units or 0, "restype": None}
        status[lot] = r["vacancy_status"]
        # SF Planning Code context for building edits: zoning district + height limit (feet)
        zc = (r.get("zoning_code") or "").split("|")[0].strip() or None
        ft = r.get("height_limit_ft")
        flags = (1 if r.get("height_special_plan") else 0) | (2 if r.get("height_open_space") else 0)
        lot_rules[lot] = (zc, int(round(ft)) if ft is not None and ft == ft else 0, flags)
        if r["vacancy_status"] == "source_indicated_vacant" and r["geometry"]:
            geoms[lot] = r["geometry"]
    places = pq.read_table(pl, columns=["place_id", "longitude", "latitude", "city", "closure_status",
                                        "primary_category"]).to_pandas()
    places = places[places["city"] == "San Francisco"]
    if not rule_cols:
        log("  WARNING: team release has no zoning/height columns; rebuild it with pipeline >= 0.1.1 + height districts")
    return landuse, status, geoms, places, lot_rules


def closed_business_buildings(places, bpolys):
    """Assign each SF Google Maps place to the footprint containing it (or within 6 m), and return
    {building index: (active, closed)}. Places with unknown closure status are ignored."""
    grid = defaultdict(list)
    C = 60.0
    for idx, ring in bpolys:
        x0, z0 = ring.min(0)
        x1, z1 = ring.max(0)
        for gx in range(int(x0 // C), int(x1 // C) + 1):
            for gz in range(int(z0 // C), int(z1 // C) + 1):
                grid[(gx, gz)].append((idx, ring, x0, z0, x1, z1))
    xs, zs = proj(places["longitude"].to_numpy(), places["latitude"].to_numpy())
    stats = defaultdict(lambda: [0, 0])
    matched = 0
    for x, z, st in zip(xs, zs, places["closure_status"].to_numpy()):
        if st not in ("active", "closed_permanently", "closed_temporarily"):
            continue
        cands = grid.get((int(x // C), int(z // C)), ())
        hit = next((i for i, r, a, b, c, d in cands if a <= x <= c and b <= z <= d and point_in_poly(x, z, r[:, 0], r[:, 1])), None)
        if hit is None:  # geocodes often sit on the sidewalk: nearest wall within 6 m
            best = 6.0
            for gx in (-1, 0, 1):
                for gz in (-1, 0, 1):
                    for i, r, a, b, c, d in grid.get((int(x // C) + gx, int(z // C) + gz), ()):
                        if x < a - 6 or x > c + 6 or z < b - 6 or z > d + 6:
                            continue
                        p0, p1 = r, np.roll(r, -1, 0)
                        dv = p1 - p0
                        t = np.clip(((x - p0[:, 0]) * dv[:, 0] + (z - p0[:, 1]) * dv[:, 1]) / np.maximum((dv ** 2).sum(1), 1e-9), 0, 1)
                        dist = np.hypot(p0[:, 0] + t * dv[:, 0] - x, p0[:, 1] + t * dv[:, 1] - z).min()
                        if dist < best:
                            best, hit = dist, i
        if hit is None:
            continue
        matched += 1
        stats[hit][0 if st == "active" else 1] += 1
    return stats, matched


def download_sources():
    for off in (0, 50000, 100000, 150000):
        fetch("https://data.sfgov.org/resource/ynuv-fyni.geojson?"
              "$select=sf16_bldgid,mblr,hgt_median_m,hgt_maxcm,hgt_meancm,gnd_min_m,shape"
              f"&$order=sf16_bldgid&$limit=50000&$offset={off}",
              os.path.join(CACHE, f"footprints_{off}.geojson"))
    fetch_overpass(os.path.join(HERE, "overpass.ql"), os.path.join(CACHE, "osm.json"))


# ---------------------------------------------------------------------------
# 4. OSM areas: landcover, lakes, piers
# ---------------------------------------------------------------------------
GRASS = {("leisure", v) for v in ("park", "garden", "golf_course", "pitch", "playground",
                                  "recreation_ground", "dog_park", "common")} | \
        {("landuse", v) for v in ("grass", "cemetery", "recreation_ground", "meadow", "village_green")} | \
        {("natural", v) for v in ("grassland", "heath")}
FOREST = {("landuse", "forest"), ("natural", "wood"), ("natural", "scrub")}
SAND = {("natural", "beach"), ("natural", "sand")}


def area_kind(tags):
    for k in ("natural", "landuse", "leisure", "man_made"):
        v = tags.get(k)
        if v is None:
            continue
        if (k, v) in FOREST:
            return "forest"
        if (k, v) in SAND:
            return "sand"
        if (k, v) in GRASS:
            return "grass"
        if k == "natural" and v == "water":
            return "water"
        if k == "man_made" and v == "pier":
            return "pier"
    return None


def to_px(xs, zs, res):
    s = res / (2 * EXT)
    return list(zip(((np.asarray(xs) + EXT) * s).tolist(), ((np.asarray(zs) + EXT) * s).tolist()))


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def main():
    log("[1/7] downloading / reading cached sources")
    download_sources()

    log("[2/7] terrain")
    H = build_terrain()
    log(f"  grid {N}x{N} @ {GRID} m, elevation {H.min():.1f} .. {H.max():.1f} m")

    with open(os.path.join(CACHE, "osm.json"), encoding="utf-8") as f:
        osm = json.load(f)["elements"]

    log("[3/7] OSM areas (landcover, lakes, piers)")
    HI = LC_RES * 2  # draw at 2x then box-downsample for anti-aliased masks
    layers = {k: Image.new("L", (HI, HI), 0) for k in ("grass", "forest", "sand")}
    draws = {k: ImageDraw.Draw(im) for k, im in layers.items()}
    lakes, piers = [], []
    for e in osm:
        tags = e.get("tags", {})
        kind = area_kind(tags)
        if kind is None or "highway" in tags:
            continue
        for outer, inners in element_polygons(e):
            ox, oz = proj([p[0] for p in outer], [p[1] for p in outer])
            if kind in layers:
                draws[kind].polygon(to_px(ox, oz, HI), fill=255)
                for r in inners:
                    ix, iz = proj([p[0] for p in r], [p[1] for p in r])
                    draws[kind].polygon(to_px(ix, iz, HI), fill=0)
            elif kind == "water":
                a = abs(signed_area(ox, oz))
                if 30.0 < a < 4.0e6:
                    lakes.append((ox, oz, [proj([p[0] for p in r], [p[1] for p in r]) for r in inners], a))
            elif kind == "pier":
                if abs(signed_area(ox, oz)) > 25.0:
                    piers.append((ox, oz))

    # Carve lakes / harbour basins into the terrain grid and record water levels.
    lake_records = []
    for ox, oz, inners, a in lakes:
        hb = hsample(H, ox, oz)
        level = float(np.percentile(hb, 25))
        mask = Image.new("L", (N, N), 0)
        d = ImageDraw.Draw(mask)
        pts = [((x + EXT) / GRID, (z + EXT) / GRID) for x, z in zip(ox, oz)]
        d.polygon(pts, fill=255)
        for ix, iz in inners:
            d.polygon([((x + EXT) / GRID, (z + EXT) / GRID) for x, z in zip(ix, iz)], fill=0)
        m = np.asarray(mask) > 0
        if level < 1.0:
            # Sea-level basin: just make sure the bay water shows through.
            H[m] = np.minimum(H[m], -2.0)
            continue
        H[m] = np.minimum(H[m], level - 1.5)
        ring = clean_ring(np.asarray(ox), np.asarray(oz), 0.8)
        if ring is None:
            continue
        holes = [r for r in (clean_ring(np.asarray(ix), np.asarray(iz), 0.8) for ix, iz in inners) if r is not None]
        lake_records.append((level, ring, holes))
    log(f"  lakes kept {len(lake_records)} (of {len(lakes)} water polygons), piers {len(piers)}")

    lc = np.stack([np.asarray(layers[k].resize((LC_RES, LC_RES), Image.BOX)) for k in ("grass", "forest", "sand")], -1)

    log("[4/7] buildings")
    landuse, lot_status, vacant_geoms, places, lot_rules = load_team_release()
    zoning_codes = sorted({z for z, _, _ in lot_rules.values() if z})[:254]
    zoning_idx = {z: i for i, z in enumerate(zoning_codes)}
    zone_out = bytearray()   # per building: uint16 height limit ft (0 = none), uint8 flags, uint8 zoning index
    cur_zone = [(0, 0, 255)]
    feats = []
    for fn in sorted(glob.glob(os.path.join(CACHE, "footprints_*.geojson")), key=lambda s: int(s.rsplit("_", 1)[1].split(".")[0])):
        with open(fn, encoding="utf-8") as f:
            feats += json.load(f)["features"]
    log(f"  footprints read: {len(feats)}, land-use parcels: {len(landuse)}")
    vac_counts_lots = Counter(lot_status.values())
    bpolys = []  # (building index, outer ring in metres) for the Places join
    log(f"  lot vacancy: {dict(vac_counts_lots)}")
    vac_out = bytearray()
    cur_vac = [0]

    osm_tall = load_osm_tall()
    log(f"  OSM buildings with heights >= 18 m: {len(osm_tall)}")
    buckets = defaultdict(list)
    for i, b in enumerate(osm_tall):
        for bx in range(int(b["x0"] // 200), int(b["x1"] // 200) + 1):
            for bz in range(int(b["z0"] // 200), int(b["z1"] // 200) + 1):
                buckets[(bx, bz)].append(i)

    def osm_at(x, z):
        for i in buckets.get((int(x // 200), int(z // 200)), ()):
            b = osm_tall[i]
            if b["x0"] <= x <= b["x1"] and b["z0"] <= z <= b["z1"] and point_in_poly(x, z, b["x"], b["z"]):
                return i
        return None

    bmask = Image.new("L", (LC_RES, LC_RES), 0)
    bdraw = ImageDraw.Draw(bmask)
    out = bytearray()
    counts = Counter()
    roof_counts = Counter()
    n_join = n_written = n_override = n_landmark = 0
    osm_matched = set()
    heights_by_type = defaultdict(list)

    def write_building(tid, height, elev, bottom, rings, roof=0.0, roof_kind=0, extra=0):
        """Record: type, rings, wall height, base elevation, wall bottom (rel.), roof rise, roof kind,
        extra (stepped: upper-block scale * 255). Roof kinds: 0 flat, 1 hipped, 2 tapered, 3 stepped."""
        nonlocal out, n_written
        out += struct.pack("<BBHhhHBB", tid, len(rings), min(65535, int(round(height * 10))),
                           int(round(elev * 10)), int(round(max(bottom, -3000) * 10)),
                           min(65535, int(round(roof * 10))), roof_kind, extra)
        for r in rings:
            out += struct.pack("<H", len(r))
            out += r.astype("<i2").tobytes()
        n_written += 1
        counts[tid] += 1
        roof_counts[roof_kind] += 1
        heights_by_type[tid].append(height + roof)
        vac_out.append(cur_vac[0])
        zone_out.extend(struct.pack("<HBB", *cur_zone[0]))

    def ground_for(outer):
        gs = hsample(H, outer[:, 0], outer[:, 1])
        gmean = float(gs.mean())
        elev = max(gmean, 1.5)                     # buildings on piers / fill stay above the bay
        bottom = min(float(gs.min()), 0.0 if gmean < 1.5 else float(gs.min())) - elev - 1.0
        return elev, bottom

    for ft in feats:
        p = ft["properties"]
        mblr = p.get("mblr") or ""
        if not (mblr.startswith("SF") or mblr.startswith("CN")):
            continue  # SM* = San Mateo County footprints just south of the city line
        g = ft.get("geometry")
        if not g:
            continue
        height = max(3.0, float(p.get("hgt_median_m") or 0))
        peak = max(height, float(p.get("hgt_maxcm") or 0) / 100.0)
        mean = max(height, float(p.get("hgt_meancm") or 0) / 100.0)
        lu = landuse.get(mblr[2:]) if mblr.startswith("SF") else None
        cur_vac[0] = TEAM_STATUS.get(lot_status.get(mblr[2:], "unknown"), VAC_UNKNOWN) if mblr.startswith("SF") else VAC_UNKNOWN
        zr = lot_rules.get(mblr[2:]) if mblr.startswith("SF") else None
        cur_zone[0] = (min(zr[1], 65535), zr[2], zoning_idx.get(zr[0], 255)) if zr else (0, 0, 255)
        if lu is not None:
            n_join += 1
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            rings = orient_rings(poly)
            if not rings:
                continue
            outer = rings[0].astype(np.float64) * Q
            area = abs(signed_area(outer[:, 0], outer[:, 1]))
            cx, cz = float(outer[:, 0].mean()), float(outer[:, 1].mean())
            if landmark_footprint(cx, cz, peak):
                n_landmark += 1
                continue
            h, pk = height, peak
            oi = osm_at(cx, cz)
            overridden = False
            if oi is not None:
                osm_matched.add(oi)
                ob = osm_tall[oi]
                # The LiDAR flight is from 2010: anything built since reads as a construction site.
                if pk < 0.6 * ob["h"] and 0.4 < area / ob["area"] < 2.5:
                    h = pk = ob["h"]
                    overridden = True
                    n_override += 1
            tid = classify(lu, h, area)
            if overridden and tid != TID["tower"] and lu is None:
                tid = osm_type(ob["tag"], h)
            roof, kind, extra = 0.0, 0, 0
            osm_h = osm_tall[oi]["h"] if oi is not None else None
            if not overridden:
                if tid == TID["tower"] and pk - h > 50 and pk > 2.5 * h:
                    h, kind = pk * 0.97, 2                     # tapered (Transamerica-style)
                elif (h >= 12 and area > 300 and pk - h > 15 and pk > 1.5 * h
                      and ((osm_h is not None and osm_h > h + 15) or (mean - h) > 0.1 * (pk - h))):
                    # Podium + tower on one footprint: podium at the median, inset block to the top.
                    top = min(pk, osm_h) if osm_h is not None and osm_h > h + 15 else pk * 0.97
                    f = float(np.clip((mean - h) / (pk - h), 0.12, 0.5))
                    extra = int(round(float(np.clip(math.sqrt(f), 0.35, 0.75)) * 255))
                    roof, kind = top - h, 3
                    tid = classify(lu, top, area)
                elif (len(rings) == 1 and len(rings[0]) == 4 and h < 20 and 1.0 <= pk - h <= 7.0
                      and tid in (TID["house"], TID["flats"], TID["apartment"], TID["civic"], TID["other"], TID["mixed"])):
                    e = np.hypot(*np.diff(np.vstack([outer, outer[:1]]), axis=0).T)
                    short = min((e[0] + e[2]) / 2, (e[1] + e[3]) / 2)
                    roof = float(np.clip(2 * (pk - h), 1.5, min(6.0, 0.55 * short)))
                    h = max(2.6, h - roof / 2)
                    kind = 1                                   # hipped roof
            elev, bottom = ground_for(outer)
            bpolys.append((n_written, outer))
            write_building(tid, h, elev, bottom, rings, roof, kind, extra)
            bdraw.polygon(to_px(outer[:, 0], outer[:, 1], LC_RES), fill=255)

    # Tall buildings completed after the 2016 footprint survey exist only in OSM.
    cur_vac[0] = 0
    cur_zone[0] = (0, 0, 255)
    n_added = 0
    for i, b in enumerate(osm_tall):
        if i in osm_matched or b["h"] < 25 or b["tag"] in ("tower", "construction", "roof"):
            continue
        lon, lat = unproj(b["x"], b["z"])
        rings = orient_rings([list(zip(lon.tolist(), lat.tolist()))])
        if not rings:
            continue
        outer = rings[0].astype(np.float64) * Q
        elev, bottom = ground_for(outer)
        bpolys.append((n_written, outer))
        write_building(osm_type(b["tag"], b["h"]), b["h"], elev, bottom, rings)
        bdraw.polygon(to_px(outer[:, 0], outer[:, 1], LC_RES), fill=255)
        n_added += 1
    log(f"  heights updated from OSM: {n_override}, buildings added from OSM: {n_added}, "
        f"landmark footprints skipped: {n_landmark}, roofs {dict(roof_counts)}")

    for ox, oz in piers:
        q = clean_ring(np.asarray(ox), np.asarray(oz), 0.4)
        if q is None or np.abs(q).max() > 32000:
            continue
        a = signed_area(q[:, 0].astype(np.float64), q[:, 1].astype(np.float64))
        if a > 0:
            q = q[::-1]
        pts = q.astype(np.float64) * Q
        gs = hsample(H, pts[:, 0], pts[:, 1])
        if gs.mean() > 3.0:
            continue  # pier mapped on land - nothing to show
        # Piers use absolute heights (elev 0): deck at 2.2 m above the bay.
        write_building(TID["pier"], 2.2, 0.0, min(-6.0, float(gs.min()) - 1.0), [q])

    with open(os.path.join(OUT, "buildings.bin"), "wb") as f:
        f.write(b"SFB2" + struct.pack("<If", n_written, Q))
        f.write(out)
    log(f"  buildings written {n_written}, land-use join {n_join}, {len(out) / 1e6:.1f} MB")
    assert len(vac_out) == n_written
    # Google Maps Places (Drive): buildings whose listed businesses are all closed
    pstats, n_matched = closed_business_buildings(places, bpolys)
    n_closed_bldg = 0
    for i, (active, closed) in pstats.items():
        if closed > 0 and active == 0 and vac_out[i] != VAC_VACANT_LOT:
            vac_out[i] = VAC_CLOSED
            n_closed_bldg += 1
    log(f"  Google Maps places in SF: {len(places)}, matched to buildings: {n_matched}, "
        f"buildings with only closed businesses: {n_closed_bldg}")
    assert len(zone_out) == 4 * n_written
    with open(os.path.join(OUT, "zoning.bin"), "wb") as f:
        f.write(b"SFZ1" + struct.pack("<I", n_written))
        f.write(zone_out)
    log(f"  zoning/height rules: {sum(1 for i in range(n_written) if zone_out[4*i+3] != 255)} buildings with a zoning district")
    with open(os.path.join(OUT, "vacancy.bin"), "wb") as f:
        f.write(b"SFV1" + struct.pack("<I", n_written))
        f.write(vac_out)
    vac_counts_bldg = Counter(vac_out)
    log(f"  building vacancy (0 unknown, 1 occupied, 2 vacant lot, 3 closed businesses): {dict(vac_counts_bldg)}")

    # Vacant lots painted on the ground (most vacant lots have no building to colour).
    vacant_lots = [l for l, st in lot_status.items() if st == "source_indicated_vacant"]
    shapes = vacant_geoms
    vimg = Image.new("L", (LC_RES * 2, LC_RES * 2), 0)
    vdraw = ImageDraw.Draw(vimg)
    for wkb in shapes.values():
        for poly in wkb_polygons(wkb):
            for k, ring in enumerate(poly):
                xs, zs = proj(ring[:, 0], ring[:, 1])
                vdraw.polygon(to_px(xs, zs, LC_RES * 2), fill=255 if k == 0 else 0)
    Image.fromarray(np.asarray(vimg.resize((LC_RES, LC_RES), Image.BOX))).save(os.path.join(OUT, "vacant.png"), optimize=True)
    log(f"  vacant lot outlines: {len(shapes)} of {len(vacant_lots)}")
    for i, t in enumerate(TYPES):
        hs = heights_by_type.get(i, [0])
        log(f"    {t[0]:<11} {counts[i]:>7}  median h {np.median(hs):5.1f} m  max {max(hs):6.1f} m")

    log("[5/7] roads")
    road_out, rmask_lines, n_roads, n_bridge = build_roads(osm, H)
    with open(os.path.join(OUT, "roads.bin"), "wb") as f:
        f.write(b"SFR1" + struct.pack("<If", n_roads, Q))
        f.write(road_out)
    log(f"  roads {n_roads} ({n_bridge} bridge pieces), {len(road_out) / 1e6:.1f} MB")

    rmask = Image.new("L", (LC_RES, LC_RES), 0)
    rdraw = ImageDraw.Draw(rmask)
    s = LC_RES / (2 * EXT)
    for xs, zs, w in rmask_lines:
        rdraw.line(to_px(xs, zs, LC_RES), fill=255, width=max(1, int(round(w * s)) + 2))

    log("[6/7] trees, urban mask, landcover")
    trees = build_trees(lc, np.asarray(bmask), np.asarray(rmask), H, lake_records)
    with open(os.path.join(OUT, "trees.bin"), "wb") as f:
        f.write(b"SFT1" + struct.pack("<If", len(trees) // 8, Q))
        f.write(trees)
    log(f"  trees {len(trees) // 8}")

    # Built-up ground = within ~55 m of a building (roads alone don't pave a hillside).
    built = np.asarray(bmask).astype(np.float32) / 255.0
    small = built.reshape(1024, 4, 1024, 4).max(axis=(1, 3))
    urban = box_blur(box_blur(small, 3) > 0.03, 2)
    Image.fromarray((np.clip(urban, 0, 1) * 255).astype(np.uint8)).save(os.path.join(OUT, "urban.png"), optimize=True)
    Image.fromarray(lc).save(os.path.join(OUT, "landcover.png"), optimize=True)

    log("[7/7] terrain + lakes + meta")
    with open(os.path.join(OUT, "terrain.bin"), "wb") as f:
        f.write(b"SFH1" + struct.pack("<Iff", N, GRID, EXT))
        f.write(np.clip(np.round(H * 10), -32000, 32000).astype("<i2").tobytes())
    lo = bytearray()
    for level, ring, holes in lake_records:
        lo += struct.pack("<hB", int(round(level * 10)), 1 + len(holes))
        for r in [ring] + holes:
            lo += struct.pack("<H", len(r)) + r.astype("<i2").tobytes()
    with open(os.path.join(OUT, "lakes.bin"), "wb") as f:
        f.write(b"SFL1" + struct.pack("<If", len(lake_records), Q))
        f.write(lo)
    meta = {
        "origin": {"lon": LON0, "lat": LAT0, "metersPerDegLon": M_LON, "metersPerDegLat": M_LAT},
        "axes": "+x east, +z south, +y up, metres",
        "extent": EXT, "grid": GRID, "terrainSize": N, "quantum": Q, "towerThresholdM": TOWER_M,
        "types": [{"id": t[0], "label": t[1], "color": t[2], "count": counts[i]} for i, t in enumerate(TYPES)],
        "zoning": {
            "codes": zoning_codes,
            "source": "Parcel Planner release parcels.geoparquet: zoning_code (DataSF parcels) and height_limit_ft "
                      "(DataSF Zoning Map - Height and Bulk Districts, h9wh-cg3m)",
        },
        "vacancy": {
            "source": "Parcel Planner data release (team Google Drive): parcels.geoparquet vacancy_status, "
                      "places.parquet closure_status",
            "rule": "vacant lot = team vacancy_status source_indicated_vacant; closed businesses = every Google Maps "
                    "place matched to the building is closed (permanently or temporarily); occupied = team "
                    "not_vacant; unknown = team unknown or no parcel match",
            "buildings": {"vacant": vac_counts_bldg[2], "closed": vac_counts_bldg[3],
                          "not_vacant": vac_counts_bldg[1], "unknown": vac_counts_bldg[0]},
            "lots": {"vacant": vac_counts_lots["source_indicated_vacant"], "not_vacant": vac_counts_lots["not_vacant"],
                     "unknown": vac_counts_lots["unknown"]},
            "placesMatched": n_matched,
            "vacantLotShapes": len(shapes),
        },
        "buildingCount": n_written, "heightsUpdatedFromOSM": n_override, "buildingsAddedFromOSM": n_added, "roadCount": n_roads, "treeCount": len(trees) // 8, "lakeCount": len(lake_records),
        "sources": [
            {"name": "DataSF Building Footprints (LiDAR heights)", "url": "https://data.sfgov.org/d/ynuv-fyni"},
            {"name": "Parcel Planner data release: parcels + Google Maps Places (team Drive)", "url": "https://github.com/Gilaze/urban_planner_helper"},
            {"name": "OpenStreetMap contributors (ODbL)", "url": "https://www.openstreetmap.org/copyright"},
            {"name": "AWS Terrain Tiles (Mapzen Terrarium; USGS 3DEP, NOAA)", "url": "https://registry.opendata.aws/terrain-tiles/"},
        ],
    }
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)

    # Debug preview of the terrain for eyeballing the coastline.
    shade = np.clip((H - H.min()) / max(1, H.max() - H.min()) * 255, 0, 255).astype(np.uint8)
    rgb = np.stack([shade, shade, shade], -1)
    rgb[H < 0.5] = [40, 90, 160]
    Image.fromarray(rgb).save(os.path.join(CACHE, "terrain_preview.png"))
    log("done ->", OUT)


def box_blur(a, r):
    a = np.asarray(a, np.float32)
    k = 2 * r + 1
    p = np.pad(a, r, mode="edge")
    c = np.cumsum(np.cumsum(np.pad(p, ((1, 0), (1, 0))), 0), 1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


# ---------------------------------------------------------------------------
# 5. Roads
# ---------------------------------------------------------------------------
ROAD_CLASS = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3, "tertiary": 4, "residential": 5,
              "unclassified": 5, "living_street": 5, "service": 6, "pedestrian": 7, "motorway_link": 8,
              "trunk_link": 8, "primary_link": 8, "secondary_link": 8, "tertiary_link": 8}
ROAD_WIDTH = {0: 11.5, 1: 14.0, 2: 14.0, 3: 12.5, 4: 11.0, 5: 9.0, 6: 4.5, 7: 6.0, 8: 6.5}
SIDEWALK = {0: 0.0, 1: 2.6, 2: 3.2, 3: 3.0, 4: 2.6, 5: 2.3, 6: 0.0, 7: 0.0, 8: 0.0}
SKIP_SERVICE = {"driveway", "parking_aisle", "drive-through", "emergency_access", "slipway", "fuel"}
GG_DECK = 67.0


def in_sf(lon, lat, tags):
    if lat < 37.7075:
        return False   # Daly City / Brisbane
    if lon < -122.455 and lat > 37.8122 and "Golden Gate Bridge" not in tags.get("name", ""):
        return False   # Marin Headlands
    return True


def densify(xs, zs, step=12.0):
    ox, oz = [xs[0]], [zs[0]]
    for i in range(1, len(xs)):
        d = math.hypot(xs[i] - xs[i - 1], zs[i] - zs[i - 1])
        n = max(1, int(math.ceil(d / step)))
        for k in range(1, n + 1):
            t = k / n
            ox.append(xs[i - 1] + (xs[i] - xs[i - 1]) * t)
            oz.append(zs[i - 1] + (zs[i] - zs[i - 1]) * t)
    return np.array(ox), np.array(oz)


def build_roads(osm, H):
    ways = []
    for e in osm:
        if e["type"] != "way":
            continue
        t = e.get("tags", {})
        hw = t.get("highway")
        if hw not in ROAD_CLASS:
            continue
        if hw == "service" and t.get("service") in SKIP_SERVICE:
            continue
        if t.get("area") == "yes" or t.get("tunnel") in ("yes", "building_passage", "culvert"):
            continue
        try:
            if int(t.get("layer", "0")) < 0:
                continue
        except ValueError:
            pass
        geom = e.get("geometry") or []
        if len(geom) < 2:
            continue
        lons = np.array([p["lon"] for p in geom])
        lats = np.array([p["lat"] for p in geom])
        if not in_sf(float(lons.mean()), float(lats.mean()), t):
            continue
        xs, zs = proj(lons, lats)
        cls = ROAD_CLASS[hw]
        oneway = t.get("oneway") in ("yes", "1", "-1") or cls == 0
        width = ROAD_WIDTH[cls]
        try:
            lanes = int(t.get("lanes", "").split(";")[0])
            width = lanes * 3.3 + (4.0 if cls in (2, 3, 4, 5) else 0.0)
        except ValueError:
            pass
        width = float(np.clip(width, 4.0, 30.0))
        bridge = t.get("bridge") not in (None, "no")
        ways.append(dict(id=e["id"], nodes=e.get("nodes", []), x=xs, z=zs, cls=cls, oneway=oneway,
                         width=width, side=SIDEWALK[cls] if not bridge else min(SIDEWALK[cls], 1.8),
                         bridge=bridge, name=t.get("name", "")))

    # Anchor nodes: endpoints of bridge ways that also belong to a non-bridge road.
    ground_nodes = set()
    for w in ways:
        if not w["bridge"]:
            ground_nodes.update(w["nodes"])
    # Network distance from anchors across the bridge graph (multi-source Dijkstra on endpoints).
    import heapq
    adj = defaultdict(list)
    for i, w in enumerate(ways):
        if w["bridge"] and len(w["nodes"]) >= 2:
            L = float(np.sum(np.hypot(np.diff(w["x"]), np.diff(w["z"]))))
            a, b = w["nodes"][0], w["nodes"][-1]
            adj[a].append((b, L))
            adj[b].append((a, L))
    dist = {}
    pq = []
    for n in adj:
        if n in ground_nodes or len(adj[n]) == 1:
            dist[n] = 0.0
            pq.append((0.0, n))
    heapq.heapify(pq)
    while pq:
        d, n = heapq.heappop(pq)
        if d > dist.get(n, 1e18):
            continue
        for m, L in adj[n]:
            nd = d + L
            if nd < dist.get(m, 1e18):
                dist[m] = nd
                heapq.heappush(pq, (nd, m))

    out = bytearray()
    lines = []
    n = 0
    nb = 0
    lim = (32000 * Q)
    for w in ways:
        xs, zs = densify(w["x"], w["z"])
        inside = (np.abs(xs) < min(lim, EXT - 30)) & (np.abs(zs) < min(lim, EXT - 30))
        if not inside.any():
            continue
        ground = hsample(H, xs, zs)
        if w["bridge"]:
            seg = np.hypot(np.diff(xs), np.diff(zs))
            s = np.concatenate([[0], np.cumsum(seg)])
            L = s[-1] if s[-1] > 0 else 1.0
            d0 = dist.get(w["nodes"][0], 0.0) if w["nodes"] else 0.0
            d1 = dist.get(w["nodes"][-1], 0.0) if w["nodes"] else 0.0
            dnet = np.minimum(d0 + s, d1 + (L - s))
            base = np.maximum(ground, 0.0)
            if "Golden Gate Bridge" in w["name"]:
                deck = np.full_like(base, GG_DECK)
            else:
                major = "Bay Bridge" in w["name"]
                target = np.where((ground < 0.5) & major, 55.0, 7.0 if w["cls"] in (0, 1, 8) else 5.0)
                deck = base + np.minimum(target, dnet * 0.06)
                # smooth along the way (~150 m window) to avoid kinks
                if len(deck) > 5:
                    k = max(1, int(150 / 12))
                    pad = np.pad(deck, k, mode="edge")
                    deck = np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
                deck = np.maximum(deck, ground + 0.6)
            elev = deck
            nb += 1
        else:
            elev = ground
        # split into runs that are inside the world window
        runs = []
        cur = []
        for i, ok in enumerate(inside):
            if ok:
                cur.append(i)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for r in runs:
            if len(r) < 2:
                continue
            qx = np.round(xs[r] / Q).astype("<i2")
            qz = np.round(zs[r] / Q).astype("<i2")
            qe = np.clip(np.round(elev[r] * 10), -32000, 32000).astype("<i2")
            flags = (1 if w["bridge"] else 0) | (2 if w["oneway"] else 0)
            out += struct.pack("<BBBBH", w["cls"], flags, int(round(w["width"] * 5)), int(round(w["side"] * 10)), len(r))
            out += np.stack([qx, qz, qe], 1).astype("<i2").tobytes()
            n += 1
            if not w["bridge"]:
                lines.append((xs[r], zs[r], w["width"] + 2 * w["side"]))
    return out, lines, n, nb


# ---------------------------------------------------------------------------
# 6. Trees (scattered in forest / park masks, avoiding roads, buildings, water)
# ---------------------------------------------------------------------------
def build_trees(lc, bmask, rmask, H, lake_records):
    rng = np.random.default_rng(42)
    px = 2 * EXT / LC_RES
    grass = lc[..., 0] > 140
    forest = lc[..., 1] > 140
    blocked = (bmask > 0) | (rmask > 0)
    # dilate blocked by 1 px
    b = blocked.copy()
    b[1:] |= blocked[:-1]
    b[:-1] |= blocked[1:]
    b[:, 1:] |= blocked[:, :-1]
    b[:, :-1] |= blocked[:, 1:]
    prob = np.where(forest, px * px / 140.0, np.where(grass, px * px / 1400.0, 0.0))
    prob[b] = 0
    pick = rng.random(prob.shape) < prob
    jj, ii = np.nonzero(pick)
    x = -EXT + (ii + rng.random(len(ii))) * px
    z = -EXT + (jj + rng.random(len(jj))) * px
    g = hsample(H, x, z)
    lon, lat = unproj(x, z)
    ok = (g > 1.0) & (lat > 37.7075) & ~((lon < -122.455) & (lat > 37.8122))
    x, z, g, isf = x[ok], z[ok], g[ok], forest[jj[ok], ii[ok]]
    if lake_records:
        lm = Image.new("L", (LC_RES, LC_RES), 0)
        d = ImageDraw.Draw(lm)
        for _, ring, _h in lake_records:
            r = ring.astype(np.float64) * Q
            d.polygon(to_px(r[:, 0], r[:, 1], LC_RES), fill=255)
        lm = np.asarray(lm)
        ci = np.clip(((x + EXT) / px).astype(int), 0, LC_RES - 1)
        cj = np.clip(((z + EXT) / px).astype(int), 0, LC_RES - 1)
        keep = lm[cj, ci] == 0
        x, z, g, isf = x[keep], z[keep], g[keep], isf[keep]
    kind = np.where(isf, (rng.random(len(x)) < 0.65).astype(np.uint8), (rng.random(len(x)) < 0.25).astype(np.uint8))
    scale = np.where(isf, rng.uniform(0.85, 1.45, len(x)), rng.uniform(0.6, 1.05, len(x)))
    rec = np.zeros(len(x), dtype=[("x", "<i2"), ("z", "<i2"), ("e", "<i2"), ("s", "u1"), ("k", "u1")])
    rec["x"] = np.round(x / Q)
    rec["z"] = np.round(z / Q)
    rec["e"] = np.round(g * 10)
    rec["s"] = np.clip(np.round(scale * 100), 1, 255)
    rec["k"] = kind
    return rec.tobytes()


if __name__ == "__main__":
    sys.exit(main())
