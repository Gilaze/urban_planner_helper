"""Paths, CRS constants and environment-driven configuration.

Everything that points at a machine-specific location or a credential comes
from environment variables so a clean checkout runs with defaults:

    PIPELINE_DATA_DIR   root for downloads and artifacts (default: <repo>/data)
    PLACES_CSV          Google Maps Places export (google_maps.csv)
    REVIEWS_CSV         Google Maps Reviews export (google_maps_reviews.csv)
    CENSUS_API_KEY      api.census.gov key (required for live ACS pulls)
    SF_PARCEL_API_BASE  Socrata base URL for SF open data (default data.sf.gov)
    SF_APP_TOKEN        optional Socrata app token (raises rate limits)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"
REPORTS_DIR = REPO_ROOT / "reports" / "data"

METRIC_CRS = "EPSG:26910"  # NAD83 / UTM 10N, meters
WGS84 = "EPSG:4326"

# Bay Area bounding box used by the Places export (README).
PLACES_BBOX = {"lat_min": 36.9, "lat_max": 38.9, "lon_min": -123.6, "lon_max": -121.55}

# Cities that drive the geographic split.
TRAIN_CITY, VALID_CITY, TEST_CITY = "Oakland", "Berkeley", "San Francisco"
SPLIT_BY_CITY = {TRAIN_CITY: "train", VALID_CITY: "validation", TEST_CITY: "test"}

# Census geography retained for access and covariates. Alameda + SF are the
# split cities; neighbours preserve coverage across city/county edges.
STATE_FIPS = "06"
COUNTY_FIPS = ["001", "013", "041", "075", "081"]  # Alameda, Contra Costa, Marin, SF, San Mateo
ACS_YEAR = 2024
TIGER_YEAR = 2024


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    places_csv: Path | None
    reviews_csv: Path | None
    census_api_key: str | None
    sf_api_base: str
    sf_app_token: str | None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("PIPELINE_DATA_DIR", REPO_ROOT / "data")).expanduser()

    def _path(name: str) -> Path | None:
        v = os.environ.get(name)
        return Path(v).expanduser() if v else None

    return Settings(
        data_dir=data_dir,
        places_csv=_path("PLACES_CSV") or _default(data_dir, "places/google_maps.csv"),
        reviews_csv=_path("REVIEWS_CSV") or _default(data_dir, "reviews/google_maps_reviews.csv"),
        census_api_key=os.environ.get("CENSUS_API_KEY") or None,
        sf_api_base=os.environ.get("SF_PARCEL_API_BASE", "https://data.sf.gov").rstrip("/"),
        sf_app_token=os.environ.get("SF_APP_TOKEN") or None,
    )


def _default(data_dir: Path, rel: str) -> Path | None:
    p = data_dir / "raw" / rel
    return p if p.exists() else None
