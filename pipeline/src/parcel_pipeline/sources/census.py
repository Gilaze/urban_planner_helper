"""ACS 2024 five-year estimates and TIGER/Line boundaries.

Variables: B01003_001 (total population), B19013_001 (median household
income, USD), B25064_001 (median gross rent, USD/month), each with its
margin of error. Block groups supply population for access; tracts supply
model covariates. Density = population / (ALAND in km²).
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from ..settings import ACS_YEAR, COUNTY_FIPS, METRIC_CRS, STATE_FIPS, TIGER_YEAR, WGS84

log = logging.getLogger(__name__)

ACS_URL = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
TIGER_URLS = {
    "block_group": f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/BG/tl_{TIGER_YEAR}_{STATE_FIPS}_bg.zip",
    "tract": f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/TRACT/tl_{TIGER_YEAR}_{STATE_FIPS}_tract.zip",
    "place": f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/PLACE/tl_{TIGER_YEAR}_{STATE_FIPS}_place.zip",
}
ACS_VARS = {
    "B01003_001E": "population", "B01003_001M": "population_moe",
    "B19013_001E": "median_household_income_usd", "B19013_001M": "median_household_income_moe_usd",
    "B25064_001E": "median_gross_rent_usd_month", "B25064_001M": "median_gross_rent_moe_usd_month",
}
# Census "annotation" sentinels (e.g. -666666666 = estimate not available).
SENTINEL_MAX = -100_000_000


class MissingCensusKey(RuntimeError):
    pass


def download(url: str, dest: Path, timeout: float = 120) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", url)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(".partial")
        with tmp.open("wb") as fh:
            for block in r.iter_content(1 << 20):
                fh.write(block)
    tmp.replace(dest)
    return dest


def load_tiger(level: str, raw_dir: Path) -> gpd.GeoDataFrame:
    url = TIGER_URLS[level]
    path = download(url, raw_dir / "census" / Path(url).name)
    with zipfile.ZipFile(path) as z:
        z.testzip()
    return gpd.read_file(f"zip://{path}")


def fetch_acs(level: str, api_key: str | None, session: requests.Session | None = None) -> pd.DataFrame:
    if not api_key:
        raise MissingCensusKey("CENSUS_API_KEY is not set; api.census.gov now rejects keyless requests")
    s = session or requests.Session()
    get = ",".join(["NAME", *ACS_VARS])
    frames = []
    for county in COUNTY_FIPS:
        if level == "block_group":
            params = {"get": get, "for": "block group:*", "in": f"state:{STATE_FIPS} county:{county} tract:*"}
        else:
            params = {"get": get, "for": "tract:*", "in": f"state:{STATE_FIPS} county:{county}"}
        params["key"] = api_key
        try:
            resp = s.get(ACS_URL, params=params, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"ACS request failed for county {county}: {type(exc).__name__}") from None
        # Never surface the request URL: it carries the API key.
        if resp.status_code != 200 or "missing_key" in resp.url or "invalid_key" in resp.url:
            raise RuntimeError(f"ACS request for county {county} failed with HTTP {resp.status_code}; "
                               "check that CENSUS_API_KEY is valid and activated")
        try:
            rows = resp.json()
        except ValueError:
            raise RuntimeError(f"ACS returned a non-JSON response for county {county}; "
                               "the key may be invalid or not yet activated") from None
        frames.append(pd.DataFrame(rows[1:], columns=rows[0]))
    return parse_acs(pd.concat(frames, ignore_index=True), level)


def parse_acs(df: pd.DataFrame, level: str) -> pd.DataFrame:
    geoid = df["state"].astype(str) + df["county"].astype(str) + df["tract"].astype(str)
    if level == "block_group":
        geoid = geoid + df["block group"].astype(str)
    out = pd.DataFrame({"geoid": geoid.astype("string")})
    for src, dst in ACS_VARS.items():
        v = pd.to_numeric(df[src], errors="coerce")
        out[dst] = v.where(v > SENTINEL_MAX)
    return out


def build_demographics(
    acs_bg: pd.DataFrame, acs_tract: pd.DataFrame, tiger_bg: gpd.GeoDataFrame, tiger_tract: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, dict]:
    parts, report = [], {}
    for level, acs, tiger in (("block_group", acs_bg, tiger_bg), ("tract", acs_tract, tiger_tract)):
        t = tiger[tiger["COUNTYFP"].isin(COUNTY_FIPS)][["GEOID", "ALAND", "COUNTYFP", "geometry"]].copy()
        t = t.rename(columns={"GEOID": "geoid", "ALAND": "land_area_m2", "COUNTYFP": "county_fips"})
        t["geoid"] = t["geoid"].astype("string")
        merged = t.merge(acs, on="geoid", how="left", indicator=True)
        report[level] = {
            "boundaries": int(len(t)),
            "acs_rows": int(len(acs)),
            "boundaries_without_acs": int((merged["_merge"] == "left_only").sum()),
            "missing": {c: int(merged[c].isna().sum()) for c in ACS_VARS.values()},
        }
        merged = merged.drop(columns="_merge")
        merged["geo_level"] = level
        parts.append(merged)
    demo = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=tiger_bg.crs).to_crs(METRIC_CRS)
    land_km2 = demo["land_area_m2"].astype(float) / 1e6
    demo["population_density_per_km2"] = (demo["population"] / land_km2).where(land_km2 > 0)
    rep = demo.geometry.representative_point()
    rep_wgs = gpd.GeoSeries(rep, crs=METRIC_CRS).to_crs(WGS84)
    demo["rep_lon"], demo["rep_lat"] = rep_wgs.x, rep_wgs.y
    demo["representative_point"] = gpd.GeoSeries(rep, crs=METRIC_CRS)
    for c in ("population", "median_household_income_usd", "median_gross_rent_usd_month"):
        demo[f"{c}_missing"] = demo[c].isna()
    demo["acs_vintage"] = f"{ACS_YEAR - 4}-{ACS_YEAR} ACS 5-year"
    demo["boundary_vintage"] = f"TIGER/Line {TIGER_YEAR}"
    demo = demo.to_crs(WGS84)
    demo["representative_point"] = demo["representative_point"].to_crs(WGS84)
    demo = demo.sort_values(["geo_level", "geoid"]).reset_index(drop=True)
    cols = ["geoid", "geo_level", "county_fips", "geometry", "representative_point", "rep_lon", "rep_lat",
            "land_area_m2",
            *ACS_VARS.values(), "population_density_per_km2", "population_missing",
            "median_household_income_usd_missing", "median_gross_rent_usd_month_missing",
            "acs_vintage", "boundary_vintage"]
    demo = demo[cols]
    # Block groups and tracts overlap: return them separately so consumers never
    # double-count residents. Block groups carry population for access; tracts
    # carry model covariates.
    bg = demo[demo["geo_level"] == "block_group"].reset_index(drop=True)
    tracts = demo[demo["geo_level"] == "tract"].reset_index(drop=True)
    return bg, tracts, report


def city_boundaries(tiger_place: gpd.GeoDataFrame, names: tuple[str, ...]) -> gpd.GeoDataFrame:
    out = tiger_place[tiger_place["NAME"].isin(names) & (tiger_place["LSAD"] == "25")]
    missing = set(names) - set(out["NAME"])
    if missing:
        raise ValueError(f"city boundaries not found: {missing}")
    return out[["NAME", "GEOID", "geometry"]].reset_index(drop=True)

