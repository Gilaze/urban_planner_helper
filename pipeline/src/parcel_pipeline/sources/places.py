"""Google Maps Places (Bright Data) ingestion and Reviews aggregation.

Places export: one row per `place_id` (147,429 rows, no duplicates observed),
`category` = main label, `all_categories` = JSON list of labels, closure flags
as True/False strings (temporarily_closed has 119 blanks). There is no city
column and no scrape timestamp, so city comes from a spatial join with Census
place boundaries and deduplication falls back to completeness + source order.

Reviews export: one row per review. Only `place_id`, `review_rating` and
`review_date` are read; reviewer identity and text are never loaded.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from ..categories import CategoryConfig
from ..settings import PLACES_BBOX, WGS84

log = logging.getLogger(__name__)

PLACES_COLUMNS = [
    "place_id", "name", "category", "all_categories", "address", "lat", "lon",
    "reviews_count", "permanently_closed", "temporarily_closed",
]
REVIEWS_COLUMNS = ["place_id", "review_rating", "review_date"]
_COMPLETENESS = ["name", "category", "address", "reviews_count", "permanently_closed", "temporarily_closed"]


def _bool(s: pd.Series) -> pd.Series:
    m = s.astype("string").str.strip().str.lower().map({"true": True, "false": False})
    return m.astype("boolean")


def clean_places_chunk(raw: pd.DataFrame, cats: CategoryConfig, row_offset: int = 0) -> tuple[pd.DataFrame, dict]:
    """Validate and classify one chunk. Returns (clean rows, rejection counts)."""
    df = raw.reindex(columns=PLACES_COLUMNS).copy()
    df["_source_row"] = np.arange(row_offset, row_offset + len(df))
    rej: dict[str, int] = {}

    df["place_id"] = df["place_id"].astype("string").str.strip()
    bad_id = df["place_id"].isna() | (df["place_id"] == "")
    rej["missing_place_id"] = int(bad_id.sum())
    df = df[~bad_id]

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    b = PLACES_BBOX
    ok = df["lat"].between(b["lat_min"], b["lat_max"]) & df["lon"].between(b["lon_min"], b["lon_max"])
    rej["invalid_coordinates"] = int((~ok).sum())
    df = df[ok]

    rc = pd.to_numeric(df["reviews_count"], errors="coerce")
    rej["invalid_reviews_count_set_null"] = int((rc < 0).sum() + (rc.notna() & (rc != rc.round())).sum())
    df["reviews_count"] = rc.where((rc >= 0) & (rc == rc.round())).astype("Int64")

    perm, temp = _bool(df["permanently_closed"]), _bool(df["temporarily_closed"])
    df["closure_status"] = np.select(
        [perm.fillna(False).to_numpy(bool), temp.fillna(False).to_numpy(bool), (perm.isna() | temp.isna()).to_numpy(bool)],
        ["permanently_closed", "temporarily_closed", "unknown"], default="open",
    )
    df["is_current_supply"] = df["closure_status"].isin(["open", "unknown"])
    df["closure_status_unknown"] = df["closure_status"] == "unknown"

    classified = [cats.classify(c, a, n) for c, a, n in zip(df["category"], df["all_categories"], df["name"])]
    df["primary_category"] = [c[0] for c in classified]
    df["service_tags"] = [c[1] for c in classified]
    df["address_city"] = df["address"].astype("string").str.extract(r",\s*([^,]+),\s*CA\b")[0].str.strip()
    df["_completeness"] = df[_COMPLETENESS].notna().sum(axis=1)
    df = df.rename(columns={"category": "source_category"})
    return df.drop(columns=["all_categories", "permanently_closed", "temporarily_closed"]), rej


def read_places(path: Path, cats: CategoryConfig, chunksize: int = 25_000) -> tuple[pd.DataFrame, dict]:
    frames, totals, n_in = [], {}, 0
    for chunk in pd.read_csv(path, usecols=lambda c: c in PLACES_COLUMNS, dtype=str, chunksize=chunksize,
                             keep_default_na=True, na_values=[""]):
        clean, rej = clean_places_chunk(chunk, cats, row_offset=n_in)
        n_in += len(chunk)
        frames.append(clean)
        for k, v in rej.items():
            totals[k] = totals.get(k, 0) + v
    df = pd.concat(frames, ignore_index=True)
    before = len(df)
    df = dedupe_places(df)
    report = {"input_rows": n_in, "rejected": totals, "duplicates_removed": before - len(df), "output_rows": len(df)}
    return df, report


def dedupe_places(df: pd.DataFrame) -> pd.DataFrame:
    """One row per place_id: most complete record first, then stable source order."""
    df = df.sort_values(["place_id", "_completeness", "_source_row"], ascending=[True, False, True], kind="stable")
    return df.drop_duplicates("place_id").drop(columns=["_completeness"]).reset_index(drop=True)


def assign_city(places: pd.DataFrame, city_bounds: gpd.GeoDataFrame) -> pd.DataFrame:
    """Spatially assign the Census place name (city) containing each coordinate."""
    pts = gpd.GeoDataFrame(places[["place_id"]], geometry=gpd.points_from_xy(places["lon"], places["lat"]), crs=WGS84)
    joined = gpd.sjoin(pts, city_bounds[["NAME", "geometry"]].to_crs(WGS84), how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    out = places.copy()
    out["city"] = joined["NAME"].reindex(out.index).astype("string")
    return out


def finalize_places(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["place_id", "name", "lon", "lat", "city", "address_city", "source_category", "primary_category",
            "service_tags", "closure_status", "closure_status_unknown", "is_current_supply", "reviews_count",
            "_source_row"]
    return df[cols].rename(columns={"_source_row": "source_row"}).sort_values("place_id").reset_index(drop=True)


def aggregate_reviews(path: Path, place_ids: set[str], chunksize: int = 100_000) -> tuple[pd.DataFrame, dict]:
    """Aggregate sampled reviews per place. Identity/text columns are never read."""
    parts, n_rows, n_invalid_rating, n_invalid_date = [], 0, 0, 0
    for chunk in pd.read_csv(path, usecols=REVIEWS_COLUMNS, dtype=str, chunksize=chunksize):
        n_rows += len(chunk)
        chunk = chunk.dropna(subset=["place_id"])
        rating = pd.to_numeric(chunk["review_rating"], errors="coerce")
        valid_rating = rating.between(1, 5) & (rating == rating.round())
        n_invalid_rating += int((~valid_rating).sum())
        date = pd.to_datetime(chunk["review_date"], errors="coerce", utc=True)
        n_invalid_date += int(date.isna().sum())
        parts.append(pd.DataFrame({
            "place_id": chunk["place_id"].str.strip(),
            "rating": rating.where(valid_rating),
            "date": date,
        }))
    r = pd.concat(parts, ignore_index=True)
    g = r.groupby("place_id", sort=True)
    summary = pd.DataFrame({
        "sampled_review_count": g.size(),
        "valid_rating_count": g["rating"].count(),
        "mean_rating": g["rating"].mean(),
        "first_review_date": g["date"].min(),
        "last_review_date": g["date"].max(),
    }).reset_index()
    summary["sampled_review_count"] = summary["sampled_review_count"].astype("int64")
    summary["valid_rating_count"] = summary["valid_rating_count"].astype("int64")
    summary["matched_to_places"] = summary["place_id"].isin(place_ids)
    report = {
        "review_rows": n_rows,
        "invalid_rating_rows": n_invalid_rating,
        "invalid_date_rows": n_invalid_date,
        "reviewed_place_ids": int(len(summary)),
        "matched_place_ids": int(summary["matched_to_places"].sum()),
        "unmatched_place_ids": int((~summary["matched_to_places"]).sum()),
        "matched_review_rows": int(summary.loc[summary["matched_to_places"], "sampled_review_count"].sum()),
    }
    return summary, report


def review_summary_for_places(places: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """One row per Places place_id (left join), zero counts where no review matched."""
    out = places[["place_id"]].merge(summary.drop(columns="matched_to_places"), on="place_id", how="left")
    out["has_sampled_reviews"] = out["sampled_review_count"].notna()
    for c in ("sampled_review_count", "valid_rating_count"):
        out[c] = out[c].fillna(0).astype("int64")
    return out.sort_values("place_id").reset_index(drop=True)
