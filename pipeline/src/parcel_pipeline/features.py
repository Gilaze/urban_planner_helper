"""Spatial features shared by training places and parcel/category scenarios.

Both tables use exactly FEATURE_COLUMNS, in this order. Distances are
straight-line meters in EPSG:26910. Supply = current (non-closed) Places with
the relevant service tag, across the whole Bay Area export so counts near city
edges are not truncated. The focal place is excluded by place_id.

No predictor is derived from reviews, ratings, popularity or the target.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

from .settings import METRIC_CRS, WGS84

RADII_SERVICE = (400, 800, 1600)
RADII_TRANSIT = (400, 800)
TRANSIT = "transit"

FEATURE_COLUMNS = [
    "category_code",
    *[f"same_category_count_{r}m" for r in RADII_SERVICE],
    *[f"other_service_count_{r}m" for r in RADII_SERVICE],
    *[f"transit_count_{r}m" for r in RADII_TRANSIT],
    "nearest_peer_distance_m",
    "population_density_per_km2",
    "median_household_income_usd",
    "median_gross_rent_usd_month",
    "nearest_peer_missing",
    "population_density_missing",
    "median_household_income_missing",
    "median_gross_rent_missing",
]
CATEGORICAL_FEATURES = ["category_code"]
FORBIDDEN_PREDICTOR_TOKENS = ("review", "rating", "popular", "reviews_count", "target", "log_count")

FEATURE_DEFINITIONS = {
    "category_code": "Index of the candidate category in config/categories.yaml `candidates` (categorical).",
    **{f"same_category_count_{r}m": f"Current supply places tagged with the category within {r} m, focal excluded."
       for r in RADII_SERVICE},
    **{f"other_service_count_{r}m": f"Current non-transit supply places not tagged with the category within {r} m, focal excluded."
       for r in RADII_SERVICE},
    **{f"transit_count_{r}m": f"Current places tagged transit within {r} m, focal excluded." for r in RADII_TRANSIT},
    "nearest_peer_distance_m": "Straight-line meters to nearest other current place tagged with the category; null if none.",
    "population_density_per_km2": "ACS B01003 tract population / TIGER tract land area (km²) at the point.",
    "median_household_income_usd": "ACS B19013 tract median household income (USD) at the point.",
    "median_gross_rent_usd_month": "ACS B25064 tract median gross rent (USD per month) at the point.",
    "nearest_peer_missing": "1 if nearest_peer_distance_m is null.",
    "population_density_missing": "1 if tract density is unavailable (no tract or null estimate).",
    "median_household_income_missing": "1 if tract income is unavailable.",
    "median_gross_rent_missing": "1 if tract rent is unavailable.",
}

_to_metric = Transformer.from_crs(WGS84, METRIC_CRS, always_xy=True)


def to_xy(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    x, y = _to_metric.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.column_stack([x, y])


@dataclass
class _Layer:
    tree: cKDTree | None
    ids: np.ndarray  # place_ids in tree order
    index: dict[str, int]

    @classmethod
    def build(cls, df: pd.DataFrame) -> "_Layer":
        if df.empty:
            return cls(None, np.array([], dtype=object), {})
        xy = to_xy(df["lon"].to_numpy(), df["lat"].to_numpy())
        ids = df["place_id"].to_numpy(dtype=object)
        return cls(cKDTree(xy), ids, {pid: i for i, pid in enumerate(ids)})

    def count(self, xy: np.ndarray, r: float, focal: np.ndarray) -> np.ndarray:
        if self.tree is None:
            return np.zeros(len(xy), dtype=np.int64)
        n = self.tree.query_ball_point(xy, r, return_length=True).astype(np.int64)
        in_layer = np.array([f in self.index for f in focal], dtype=bool) if len(focal) else np.zeros(len(xy), bool)
        if in_layer.any():
            # The focal place sits at distance 0 from itself, so it is always counted once.
            n = n - in_layer.astype(np.int64)
        return n

    def nearest_other(self, xy: np.ndarray, focal: np.ndarray) -> np.ndarray:
        if self.tree is None or len(self.ids) == 0:
            return np.full(len(xy), np.nan)
        k = min(2, len(self.ids))
        d, idx = self.tree.query(xy, k=k)
        if k == 1:
            d, idx = d[:, None], idx[:, None]
        out = d[:, 0].copy()
        first_is_focal = np.array([self.ids[i] == f if f is not None else False for i, f in zip(idx[:, 0], focal)])
        if k == 2:
            out[first_is_focal] = d[first_is_focal, 1]
        else:
            out[first_is_focal] = np.inf
        out[~np.isfinite(out)] = np.nan
        return out


class SupplyIndex:
    """KD-trees over current supply, one per service tag plus the 'other service' complement."""

    def __init__(self, places: pd.DataFrame, candidates: tuple[str, ...]):
        supply = places[places["is_current_supply"]].reset_index(drop=True)
        tags = supply["service_tags"].apply(set)
        self.candidates = candidates
        self.by_tag = {c: _Layer.build(supply[tags.apply(lambda t, c=c: c in t)]) for c in candidates}
        self.transit = _Layer.build(supply[tags.apply(lambda t: TRANSIT in t)])
        non_transit = supply[~tags.apply(lambda t: TRANSIT in t)]
        nt_tags = tags[non_transit.index]
        self.other = {c: _Layer.build(non_transit[~nt_tags.apply(lambda t, c=c: c in t)]) for c in candidates}


def spatial_features(
    points: pd.DataFrame, category: str, index: SupplyIndex, tracts: gpd.GeoDataFrame | None
) -> pd.DataFrame:
    """points: columns lon, lat and optional focal_place_id. Returns FEATURE_COLUMNS frame."""
    xy = to_xy(points["lon"].to_numpy(), points["lat"].to_numpy())
    focal = (points["focal_place_id"].to_numpy(dtype=object) if "focal_place_id" in points
             else np.full(len(points), None, dtype=object))
    out = pd.DataFrame(index=points.index)
    out["category_code"] = index.candidates.index(category)
    for r in RADII_SERVICE:
        out[f"same_category_count_{r}m"] = index.by_tag[category].count(xy, r, focal)
    for r in RADII_SERVICE:
        out[f"other_service_count_{r}m"] = index.other[category].count(xy, r, focal)
    for r in RADII_TRANSIT:
        out[f"transit_count_{r}m"] = index.transit.count(xy, r, focal)
    out["nearest_peer_distance_m"] = index.by_tag[category].nearest_other(xy, focal)
    cov = tract_covariates(points, tracts)
    for c in ("population_density_per_km2", "median_household_income_usd", "median_gross_rent_usd_month"):
        out[c] = cov[c].to_numpy()
    out["nearest_peer_missing"] = out["nearest_peer_distance_m"].isna().astype(np.int8)
    out["population_density_missing"] = out["population_density_per_km2"].isna().astype(np.int8)
    out["median_household_income_missing"] = out["median_household_income_usd"].isna().astype(np.int8)
    out["median_gross_rent_missing"] = out["median_gross_rent_usd_month"].isna().astype(np.int8)
    return out[FEATURE_COLUMNS]


def tract_covariates(points: pd.DataFrame, tracts: gpd.GeoDataFrame | None) -> pd.DataFrame:
    cols = ["tract_geoid", "population_density_per_km2", "median_household_income_usd", "median_gross_rent_usd_month"]
    if tracts is None or tracts.empty:
        return pd.DataFrame(np.nan, index=points.index, columns=cols)
    pts = gpd.GeoDataFrame(index=points.index, geometry=gpd.points_from_xy(points["lon"], points["lat"]), crs=WGS84)
    t = tracts[["geoid", *cols[1:], "geometry"]].to_crs(WGS84)
    j = gpd.sjoin(pts, t, how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")].rename(columns={"geoid": "tract_geoid"})
    return j.reindex(points.index)[cols]


def build_place_features(places: pd.DataFrame, index: SupplyIndex, tracts, split_by_city: dict[str, str]) -> pd.DataFrame:
    """One row per training-eligible place: split cities, candidate primary, not permanently closed."""
    elig = places[
        places["city"].isin(split_by_city.keys())
        & places["primary_category"].isin(index.candidates)
        & (places["closure_status"] != "closed_permanently")
    ].sort_values("place_id")
    frames = []
    for cat in index.candidates:
        sub = elig[elig["primary_category"] == cat]
        if sub.empty:
            continue
        pts = sub[["lon", "lat"]].assign(focal_place_id=sub["place_id"].to_numpy())
        f = spatial_features(pts, cat, index, tracts)
        meta = pd.DataFrame({
            "place_id": sub["place_id"].to_numpy(),
            "category": cat,
            "city": sub["city"].astype(str).to_numpy(),
            "split": sub["city"].map(split_by_city).to_numpy(),
            "reviews_count": sub["reviews_count"].to_numpy(),
        }, index=sub.index)
        frames.append(pd.concat([meta, f], axis=1))
    df = pd.concat(frames, ignore_index=True)
    rc = pd.to_numeric(df["reviews_count"], errors="coerce")
    df["target_log1p_reviews_count"] = np.log1p(rc)
    df["labeled"] = rc.notna()
    return df.sort_values(["category", "place_id"]).reset_index(drop=True)


def build_parcel_features(parcels: gpd.GeoDataFrame, index: SupplyIndex, tracts) -> pd.DataFrame:
    """One row per parcel x candidate category (no target, no split)."""
    usable = parcels[parcels["rep_lon"].notna()].sort_values("parcel_id")
    pts = usable[["rep_lon", "rep_lat"]].rename(columns={"rep_lon": "lon", "rep_lat": "lat"})
    cov = tract_covariates(pts, tracts)  # same for every category; compute once
    frames = []
    for cat in index.candidates:
        f = spatial_features(pts, cat, index, None)
        for c in ("population_density_per_km2", "median_household_income_usd", "median_gross_rent_usd_month"):
            f[c] = cov[c].to_numpy()
        f["population_density_missing"] = f["population_density_per_km2"].isna().astype(np.int8)
        f["median_household_income_missing"] = f["median_household_income_usd"].isna().astype(np.int8)
        f["median_gross_rent_missing"] = f["median_gross_rent_usd_month"].isna().astype(np.int8)
        meta = pd.DataFrame({"parcel_id": usable["parcel_id"].to_numpy(), "category": cat,
                             "tract_geoid": cov["tract_geoid"].to_numpy()}, index=usable.index)
        frames.append(pd.concat([meta, f[FEATURE_COLUMNS]], axis=1))
    return pd.concat(frames, ignore_index=True).sort_values(["parcel_id", "category"]).reset_index(drop=True)


def assert_no_leakage(columns: list[str]) -> None:
    bad = [c for c in columns if any(tok in c.lower() for tok in FORBIDDEN_PREDICTOR_TOKENS)]
    if bad:
        raise AssertionError(f"target-derived predictors present: {bad}")
