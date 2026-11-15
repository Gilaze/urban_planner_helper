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
ASSESSOR_ID = "wv5m-vpq2"
LANDUSE_ID = "fdfd-xptc"
LANDUSE_VINTAGE = "2023"

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

    # Land use: physical-lot grain, explicit vacancy code only.
    lu = landuse.set_index("mapblklot")
    lots = lots.join(lu[["landuse", "resunits", "totalcomm"]], on="mapblklot")
    lots = lots.rename(columns={"landuse": "existing_use", "resunits": "residential_units",
                                "totalcomm": "commercial_area_sqft"})
    lots["vacancy_status"] = np.select(
        [lots["existing_use"].isin(VACANT_CODES), lots["existing_use"].notna()],
        ["source_indicated_vacant", "not_vacant"], default="unknown",
    )
    lots["site_label"] = np.where(lots["vacancy_status"] == "source_indicated_vacant",
                                  "source_indicated_vacancy", "hypothetical")

    # Assessor: APN grain -> sum values over all member APNs (condos), keep nulls.
    apn_to_lot = lots[["mapblklot", "source_apns"]].explode("source_apns").rename(columns={"source_apns": "apn"})
    a = apn_to_lot.merge(assessor, on="apn", how="left")
    matched = a["assessor_roll_year"].notna()

    def _sum_or_null(s: pd.Series) -> float | None:
        return s.sum() if s.notna().any() else np.nan

    agg = a.groupby("mapblklot").agg(
        assessed_land_value_usd=("assessed_land_value_usd", _sum_or_null),
        assessed_improvement_value_usd=("assessed_improvement_value_usd", _sum_or_null),
        assessor_lot_area_sqft=("lot_area_sqft", "max"),
        assessor_use_codes=("use_code", lambda s: sorted(set(s.dropna()))),
        assessor_matched_apns=("assessor_roll_year", lambda s: int(s.notna().sum())),
    )
    lots = lots.join(agg, on="mapblklot")
    roll_years = sorted(set(assessor["assessor_roll_year"].dropna()))
    lots["assessor_roll_year"] = roll_years[-1] if roll_years else None
    lots.loc[lots["assessor_matched_apns"] == 0, "assessor_roll_year"] = None

    lots["parcel_id"] = lots["mapblklot"].map(parcel_id)
    lots["parcel_source_vintage"] = parcel_vintage
    lots["landuse_vintage"] = landuse_vintage
    lots = lots.to_crs(WGS84)

    cols = [
        "parcel_id", "mapblklot", "source_apns", "apn_count", "geometry", "geometry_quality",
        "rep_lon", "rep_lat", "area_m2", "existing_use", "vacancy_status", "site_label",
        "zoning_code", "zoning_district", "analysis_neighborhood", "residential_units",
        "commercial_area_sqft", "assessed_land_value_usd", "assessed_improvement_value_usd",
        "assessor_lot_area_sqft", "assessor_use_codes", "assessor_matched_apns", "assessor_roll_year",
        "parcel_source_vintage", "landuse_vintage",
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
        "assessor_rows": int(len(assessor)),
        "assessor_matched_apns": int(matched.sum()),
        "assessor_unmatched_active_apns": int((~matched).sum()),
        "assessor_rows_without_active_apn": int(len(set(assessor["apn"]) - set(active["blklot"]))),
        "lots_with_any_assessor_match": int((lots["assessor_matched_apns"] > 0).sum()),
        "assessor_roll_years": roll_years,
    }
    return lots, report
