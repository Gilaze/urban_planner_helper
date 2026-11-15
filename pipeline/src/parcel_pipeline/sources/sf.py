"""San Francisco parcel, land-use and assessor adapters.

Verified live schemas (data.sf.gov, 2026-11-14):

* Parcels – Active and Retired (acdm-wktn): one row per `blklot` (APN). Condo
  units share a `mapblklot` (physical lot); 227,968 active blklots map to
  155,028 mapblklots. Every blklot carries geometry (262 active rows lack it).
* Assessor Historical Secured Property Tax Rolls (wv5m-vpq2): one row per
  `parcel_number` x `closed_roll_year`; `parcel_number` uses the blklot format
  (e.g. "0012003A"). Latest closed roll: 2025.
* San Francisco Land Use – 2023 (fdfd-xptc, archived): one row per mapblklot
  (one duplicate). `landuse` has an explicit `VACANT` code (8,512 lots).
* San Francisco Land Use, current (c5ge-t6pj, updated 2026-08-17): rows by
  `geography_type` (parcel / multiple_parcels / analytical); floor area per use
  and residential units, but NO explicit vacancy code. 4,592 of the 2023
  VACANT lots still show no recorded use; the rest were developed since.

Vacancy rule: `source_indicated_vacant` requires the explicit 2023 VACANT code
AND no recorded use in the current snapshot. Any current recorded use ->
`not_vacant`. Everything else -> `unknown`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape

from ..settings import METRIC_CRS, WGS84

log = logging.getLogger(__name__)

PARCELS_ID = "acdm-wktn"
HEIGHT_ID = "h9wh-cg3m"  # Zoning Map - Height and Bulk Districts (Planning Code height limits, feet)
HEIGHT_FIELDS = ["height", "gen_hght", "the_geom"]
FT_TO_M = 0.3048
ASSESSOR_ID = "wv5m-vpq2"
LANDUSE_ID = "fdfd-xptc"
LANDUSE_VINTAGE = "2023"
CURRENT_LANDUSE_ID = "c5ge-t6pj"
CURRENT_LANDUSE_FIELDS = ["mapblklot", "ludb_id", "geography_type", "resunits", "cie", "med", "mips", "retail",
                          "pdr", "visitor", "total_comm", "parking_lo", "garage", "open_space", "data_as_of"]
_USE_AREAS = ["cie", "med", "mips", "retail", "pdr", "visitor"]

PARCEL_FIELDS = [
    "blklot", "mapblklot", "zoning_code", "zoning_district", "active", "shape",
    "analysis_neighborhood", "data_as_of",
]
ASSESSOR_FIELDS = [
    "parcel_number", "closed_roll_year", "use_code", "use_definition", "lot_area",
    "assessed_land_value", "assessed_improvement_value", "number_of_units",
]
LANDUSE_FIELDS = ["mapblklot", "landuse", "resunits", "totalcomm"]

VACANT_CODES = {"VACANT"}

VACANCY_STATUSES = ("source_indicated_vacant", "unknown", "not_vacant")
# A lot at least this share under Census water (bay, ocean, lakes) is treated as submerged:
# SF land use codes many tideland parcels VACANT, but they are not buildable sites.
SUBMERGED_WATER_FRACTION = 0.5


def parcel_id(mapblklot: str) -> str:
    return f"sf:{mapblklot}"


def _apn(v: object) -> str | None:
    """APNs stay strings: preserve leading zeros and letter suffixes."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip().upper()
    return s or None


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _geom(v: object):
    if not isinstance(v, dict) or "type" not in v:
        return None
    try:
        return shape(v)
    except Exception:  # malformed upstream geometry
        return None


def parse_parcel_records(records: Iterable[dict]) -> gpd.GeoDataFrame:
    """API records (one per blklot) -> GeoDataFrame in WGS84. Drops malformed keys."""
    rows = []
    for r in records:
        blklot, mapblklot = _apn(r.get("blklot")), _apn(r.get("mapblklot"))
        if not blklot or not mapblklot:
            continue
        rows.append({
            "blklot": blklot,
            "mapblklot": mapblklot,
            "zoning_code": r.get("zoning_code") or None,
            "zoning_district": r.get("zoning_district") or None,
            "active": str(r.get("active")).lower() == "true",
            "analysis_neighborhood": r.get("analysis_neighborhood") or None,
            "data_as_of": r.get("data_as_of") or None,
            "geometry": _geom(r.get("shape")),
        })
    cols = ["blklot", "mapblklot", "zoning_code", "zoning_district", "active",
            "analysis_neighborhood", "data_as_of", "geometry"]
    df = gpd.GeoDataFrame(pd.DataFrame(rows, columns=cols), geometry="geometry", crs=WGS84)
    return df.sort_values("blklot", kind="stable").reset_index(drop=True)


def parse_landuse_records(records: Iterable[dict]) -> pd.DataFrame:
    df = pd.DataFrame([
        {"mapblklot": _apn(r.get("mapblklot")), "landuse": (r.get("landuse") or None),
         "resunits": _num(r.get("resunits")), "totalcomm": _num(r.get("totalcomm"))}
        for r in records
    ], columns=LANDUSE_FIELDS)
    df = df.dropna(subset=["mapblklot"])
    # Deduplicate: prefer rows with a land-use code, then stable source order.
    df["_has"] = df["landuse"].notna()
    df = df.sort_values(["mapblklot", "_has"], ascending=[True, False], kind="stable")
    return df.drop_duplicates("mapblklot").drop(columns="_has").reset_index(drop=True)


def _flag(v: object) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes"}


def parse_height_records(records: Iterable[dict]) -> gpd.GeoDataFrame:
    """Height and Bulk Districts -> district label, numeric limit in feet, open-space flag.

    `gen_hght` is the generalized limit in feet. Repeated-digit codes (2222 ... 9999) are not
    heights: they mark districts whose height is set elsewhere - OS open space (9999),
    Candlestick Point (5555), Hunters Point (7777), Special Height Districts (4444) and similar.
    Those get a null limit, `height_special_plan` true, and `height_open_space` for OS.
    """
    rows = []
    for r in records:
        g = _geom(r.get("the_geom"))
        if g is None:
            continue
        label = (r.get("height") or "").strip() or None
        ft = _num(r.get("gen_hght"))
        special = ft is not None and ft >= 1111 and ft % 1111 == 0
        open_space = (label or "").upper().startswith("OS") or ft == 9999
        rows.append({"height_district": label, "height_limit_ft": None if (special or open_space) else ft,
                     "height_open_space": open_space, "height_special_plan": special or open_space, "geometry": g})
    cols = ["height_district", "height_limit_ft", "height_open_space", "height_special_plan", "geometry"]
    return gpd.GeoDataFrame(pd.DataFrame(rows, columns=cols), geometry="geometry", crs=WGS84)


def parse_current_landuse_records(records: Iterable[dict]) -> pd.DataFrame:
    """Current land use, physical-lot rows only, one row per mapblklot."""
    rows = []
    for r in records:
        lot = _apn(r.get("mapblklot"))
        if not lot or r.get("geography_type") != "parcel":
            continue
        areas = {k: _num(r.get(k)) or 0.0 for k in _USE_AREAS}
        units = _num(r.get("resunits")) or 0.0
        flags = {"parking": _flag(r.get("parking_lo")) or _flag(r.get("garage")),
                 "open_space": _flag(r.get("open_space"))}
        uses = (["residential"] if units > 0 else []) + [k for k, v in areas.items() if v > 0] + \
               [k for k, v in flags.items() if v]
        rows.append({
            "mapblklot": lot,
            "ludb_id": r.get("ludb_id") or "",
            "current_uses": ",".join(uses) if uses else None,
            "current_residential_units": units,
            "current_commercial_area_sqft": _num(r.get("total_comm")) or 0.0,
            "current_has_recorded_use": bool(uses) or (_num(r.get("total_comm")) or 0) > 0,
            "current_landuse_as_of": r.get("data_as_of") or None,
        })
    cols = ["mapblklot", "ludb_id", "current_uses", "current_residential_units", "current_commercial_area_sqft",
            "current_has_recorded_use", "current_landuse_as_of"]
    df = pd.DataFrame(rows, columns=cols).sort_values(["mapblklot", "ludb_id"], kind="stable")
    # A handful of lots have more than one parcel row: any recorded use wins.
    agg = df.groupby("mapblklot", sort=True).agg(
        current_uses=("current_uses", lambda s: ",".join(sorted({u for x in s.dropna() for u in x.split(",")})) or None),
        current_residential_units=("current_residential_units", "sum"),
        current_commercial_area_sqft=("current_commercial_area_sqft", "sum"),
        current_has_recorded_use=("current_has_recorded_use", "any"),
        current_landuse_as_of=("current_landuse_as_of", "max"),
    )
    return agg.reset_index()


def parse_assessor_records(records: Iterable[dict], roll_year: str) -> pd.DataFrame:
    rows = []
    for r in records:
        if str(r.get("closed_roll_year")) != str(roll_year):
            continue
        apn = _apn(r.get("parcel_number"))
        if not apn:
            continue
        rows.append({
            "apn": apn,
            "assessor_roll_year": str(roll_year),
            "use_code": r.get("use_code") or None,
            "lot_area_sqft": _num(r.get("lot_area")),
            "assessed_land_value_usd": _num(r.get("assessed_land_value")),
            "assessed_improvement_value_usd": _num(r.get("assessed_improvement_value")),
            "number_of_units": _num(r.get("number_of_units")),
        })
    cols = ["apn", "assessor_roll_year", "use_code", "lot_area_sqft", "assessed_land_value_usd",
            "assessed_improvement_value_usd", "number_of_units"]
    df = pd.DataFrame(rows, columns=cols)
    # Multiple roll rows for one APN in one year (rare) -> keep first in stable order.
    return df.sort_values("apn", kind="stable").drop_duplicates("apn").reset_index(drop=True)


def _clean_polygon(g):
    """Return (polygonal geometry or None, quality flag)."""
    if g is None or g.is_empty:
        return None, "missing"
    if g.is_valid and g.geom_type in ("Polygon", "MultiPolygon"):
        return g, "valid"
    fixed = shapely.make_valid(g)
    polys = [p for p in getattr(fixed, "geoms", [fixed]) if p.geom_type in ("Polygon", "MultiPolygon")]
    if not polys:
        return None, "unusable"
    out = shapely.union_all(polys)
    return (out, "repaired") if not out.is_empty else (None, "unusable")


def build_physical_parcels(
    parcels: gpd.GeoDataFrame,
    landuse: pd.DataFrame,
    assessor: pd.DataFrame,
    parcel_vintage: str | None,
    landuse_vintage: str = LANDUSE_VINTAGE,
    current_landuse: pd.DataFrame | None = None,
    water: gpd.GeoDataFrame | None = None,
    heights: gpd.GeoDataFrame | None = None,
) -> tuple[gpd.GeoDataFrame, dict]:
    """Consolidate APN rows to one row per physical lot (mapblklot).

    Returns the canonical parcels table and a join-coverage report.
    """
    active = parcels[parcels["active"]].copy()
    metric = active.to_crs(METRIC_CRS)

    # Physical lot geometry = union of all member APN polygons in metric CRS.
    cleaned = metric.geometry.apply(_clean_polygon)
    metric["geometry"] = [c[0] for c in cleaned]
    metric["_q"] = [c[1] for c in cleaned]

    metric = metric.sort_values(["mapblklot", "blklot"], kind="stable")
    g = metric.groupby("mapblklot", sort=True)
    attrs = g[["zoning_code", "zoning_district", "analysis_neighborhood"]].first()
    attrs["source_apns"] = g["blklot"].agg(list)
    attrs["_all_valid"] = g["_q"].agg(lambda s: (s == "valid").all())

    # Union member polygons per lot; single-APN lots skip the union.
    has_geom = metric[metric.geometry.notna()]
    sizes = has_geom.groupby("mapblklot").size()
    single = has_geom[has_geom["mapblklot"].isin(sizes.index[sizes == 1])].set_index("mapblklot").geometry
    multi_rows = has_geom[has_geom["mapblklot"].isin(sizes.index[sizes > 1])]
    multi = pd.Series(
        {k: shapely.union_all(v.values) for k, v in multi_rows.groupby("mapblklot").geometry},
        dtype=object,
    )
    geom = pd.concat([single, multi]) if len(multi) else single
    attrs["geometry"] = geom.reindex(attrs.index)
    attrs["geometry_quality"] = np.where(
        attrs["geometry"].isna(), "unusable", np.where(attrs["_all_valid"], "valid", "repaired")
    )
    lots = gpd.GeoDataFrame(attrs.drop(columns="_all_valid").reset_index(), geometry="geometry", crs=METRIC_CRS)
    lots["apn_count"] = lots["source_apns"].str.len()
    lots["area_m2"] = lots.geometry.area.where(lots.geometry.notna())
    rep = lots.geometry.representative_point()
    rep_wgs = gpd.GeoSeries(rep, crs=METRIC_CRS).to_crs(WGS84)
    lots["rep_lon"] = rep_wgs.x.where(lots.geometry.notna())
    lots["rep_lat"] = rep_wgs.y.where(lots.geometry.notna())
    lots["representative_point"] = gpd.GeoSeries(rep.where(lots.geometry.notna()), crs=METRIC_CRS)

    # Land use: physical-lot grain, explicit vacancy code only.
    lu = landuse.set_index("mapblklot")
    lots = lots.join(lu[["landuse", "resunits", "totalcomm"]], on="mapblklot")
    lots = lots.rename(columns={"landuse": "landuse_2023", "resunits": "residential_units_2023",
                                "totalcomm": "commercial_area_sqft_2023"})
    if current_landuse is None:
        current_landuse = pd.DataFrame(columns=["mapblklot", "current_uses", "current_residential_units",
                                                "current_commercial_area_sqft", "current_has_recorded_use",
                                                "current_landuse_as_of"])
    lots = lots.join(current_landuse.set_index("mapblklot"), on="mapblklot")
    in_current = lots["current_has_recorded_use"].notna()
    has_use = lots["current_has_recorded_use"].eq(True)
    vacant_2023 = lots["landuse_2023"].isin(VACANT_CODES)
    lots["vacancy_status"] = np.select(
        [has_use, vacant_2023 & in_current & ~has_use, lots["landuse_2023"].notna() & ~vacant_2023 & ~in_current],
        ["not_vacant", "source_indicated_vacant", "not_vacant"], default="unknown",
    )
    lots["vacancy_evidence"] = np.select(
        [has_use & vacant_2023, has_use, vacant_2023 & in_current, vacant_2023,
         in_current, lots["landuse_2023"].notna()],
        ["2023 VACANT but current snapshot records use (developed since)",
         "current snapshot records use",
         "2023 VACANT code and no recorded use in current snapshot",
         "2023 VACANT code; lot absent from current snapshot",
         "no recorded use in current snapshot; no explicit vacancy code",
         "2023 land use records use; lot absent from current snapshot"],
        default="no land-use record",
    )
    # Submerged lots: never a vacancy candidate, whatever the land-use code says.
    lots["water_fraction"] = 0.0
    if water is not None and len(water):
        wat = water.to_crs(METRIC_CRS).geometry.union_all()
        touch = lots.geometry.notna() & lots.geometry.intersects(wat)
        if touch.any():
            g = lots.loc[touch, "geometry"]
            lots.loc[touch, "water_fraction"] = (g.intersection(wat).area / g.area).clip(0, 1).to_numpy()
    lots["submerged"] = lots["water_fraction"] >= SUBMERGED_WATER_FRACTION

    # Planning Code height limit at the lot's representative point (Height and Bulk Districts).
    lots["height_district"] = None
    lots["height_limit_ft"] = np.nan
    lots["height_open_space"] = False
    lots["height_special_plan"] = False
    if heights is not None and len(heights):
        pts = gpd.GeoDataFrame({"_i": lots.index}, geometry=lots.geometry.representative_point(), crs=METRIC_CRS)
        pts = pts[lots.geometry.notna().to_numpy()]
        hj = gpd.sjoin(pts, heights.to_crs(METRIC_CRS), how="left", predicate="within")
        hj = hj[~hj.index.duplicated(keep="first")].set_index("_i")
        lots.loc[hj.index, "height_district"] = hj["height_district"].to_numpy()
        lots.loc[hj.index, "height_limit_ft"] = pd.to_numeric(hj["height_limit_ft"], errors="coerce").to_numpy()
        lots.loc[hj.index, "height_open_space"] = hj["height_open_space"].eq(True).to_numpy()
        lots.loc[hj.index, "height_special_plan"] = hj["height_special_plan"].eq(True).to_numpy()
    lots["height_limit_m"] = (lots["height_limit_ft"] * FT_TO_M).round(1)
    wet_vacant = lots["submerged"] & lots["vacancy_status"].eq("source_indicated_vacant")
    lots.loc[wet_vacant, "vacancy_status"] = "unknown"
    lots.loc[wet_vacant, "vacancy_evidence"] = "2023 VACANT code but lot is mostly under water (submerged)"
    lots["existing_use"] = lots["current_uses"].where(in_current, lots["landuse_2023"])
    lots.loc[in_current & ~has_use, "existing_use"] = "no recorded use"
    lots["site_label"] = np.where(lots["vacancy_status"] == "source_indicated_vacant",
                                  "source_indicated_vacancy", "hypothetical")

    # An unknown member APN value makes that physical-lot total unknown.
    apn_to_lot = lots[["mapblklot", "source_apns"]].explode("source_apns").rename(columns={"source_apns": "apn"})
    a = apn_to_lot.merge(assessor, on="apn", how="left")
    matched = a["assessor_roll_year"].notna()

    def _sum_or_null(s: pd.Series) -> float | None:
        return s.sum() if s.notna().all() else np.nan

    agg = a.groupby("mapblklot").agg(
        assessed_land_usd=("assessed_land_value_usd", _sum_or_null),
        assessed_improvement_usd=("assessed_improvement_value_usd", _sum_or_null),
        assessor_lot_area_sqft=("lot_area_sqft", "max"),
        assessor_use_codes=("use_code", lambda s: sorted(set(s.dropna()))),
        assessor_matched_apns=("assessor_roll_year", lambda s: int(s.notna().sum())),
    )
    lots = lots.join(agg, on="mapblklot")
    roll_years = sorted(set(assessor["assessor_roll_year"].dropna()))
    year = int(roll_years[-1]) if roll_years else None
    lots["assessment_year"] = pd.array([year] * len(lots), dtype="Int64")
    lots.loc[lots["assessor_matched_apns"] == 0, "assessment_year"] = pd.NA

    lots["parcel_id"] = lots["mapblklot"].map(parcel_id)
    lots["parcel_source_vintage"] = parcel_vintage
    lots["landuse_vintage"] = landuse_vintage
    lots["zoning"] = lots["zoning_code"]
    cur_asof = current_landuse["current_landuse_as_of"].dropna().max() if len(current_landuse) else None
    lots["current_landuse_vintage"] = cur_asof
    lots["source_vintage"] = (f"parcels {parcel_vintage}; land use {str(cur_asof)[:10] if cur_asof else 'n/a'} "
                              f"(vacancy code {landuse_vintage})")
    lots = lots.to_crs(WGS84)
    lots["representative_point"] = lots["representative_point"].to_crs(WGS84)

    cols = [
        "parcel_id", "mapblklot", "source_apns", "apn_count", "geometry", "geometry_quality",
        "representative_point", "rep_lon", "rep_lat", "area_m2", "existing_use", "vacancy_status",
        "vacancy_evidence", "water_fraction", "submerged", "height_district", "height_limit_ft", "height_limit_m",
        "height_open_space", "height_special_plan", "site_label", "zoning", "zoning_code", "zoning_district", "analysis_neighborhood",
        "current_uses", "current_residential_units", "current_commercial_area_sqft", "landuse_2023",
        "residential_units_2023", "commercial_area_sqft_2023", "assessed_land_usd", "assessed_improvement_usd",
        "assessor_lot_area_sqft", "assessor_use_codes", "assessor_matched_apns", "assessment_year",
        "source_vintage", "parcel_source_vintage", "current_landuse_vintage", "landuse_vintage",
    ]
    lots = lots[cols].sort_values("parcel_id").reset_index(drop=True)

    lu_keys = set(landuse["mapblklot"])
    lot_keys = set(lots["mapblklot"])
    report = {
        "grain": {"parcels_input": "blklot (APN)", "parcels_output": "mapblklot (physical lot)",
                  "landuse": "mapblklot", "assessor": "parcel_number x closed_roll_year"},
        "active_apns": int(len(active)),
        "physical_lots": int(len(lots)),
        "condo_lots": int((lots["apn_count"] > 1).sum()),
        "geometry_quality": lots["geometry_quality"].value_counts().to_dict(),
        "landuse_rows": int(len(landuse)),
        "landuse_matched_lots": len(lot_keys & lu_keys),
        "landuse_unmatched_rows": len(lu_keys - lot_keys),
        "vacancy_status": lots["vacancy_status"].value_counts().to_dict(),
        "vacancy_evidence": lots["vacancy_evidence"].value_counts().to_dict(),
        "submerged_lots": int(lots["submerged"].sum()),
        "submerged_lots_reclassified_from_vacant": int(wet_vacant.sum()),
        "water_layer": "provided" if water is not None else "missing",
        "lots_with_height_limit": int(lots["height_limit_ft"].notna().sum()),
        "lots_in_open_space_height_district": int(lots["height_open_space"].sum()),
        "lots_in_special_plan_height_district": int(lots["height_special_plan"].sum()),
        "lots_without_height_district": int(lots["height_district"].isna().sum()),
        "current_landuse_rows": int(len(current_landuse)),
        "current_landuse_matched_lots": int(in_current.sum()),
        "assessor_rows": int(len(assessor)),
        "assessor_matched_apns": int(matched.sum()),
        "assessor_unmatched_active_apns": int((~matched).sum()),
        "assessor_rows_without_active_apn": int(len(set(assessor["apn"]) - set(active["blklot"]))),
        "lots_with_any_assessor_match": int((lots["assessor_matched_apns"] > 0).sum()),
        "assessor_roll_years": roll_years,
    }
    return lots, report
