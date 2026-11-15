import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
from threading import Lock
from time import monotonic

import httpx
from shapely.errors import GEOSException
from shapely.geometry import box, shape

from .geometry import parcel_shape, polygon_model, project, validate_wgs84
from .schemas import ParcelFeature, ParcelProperties, ParcelSource, Point

PARCEL_URL = "https://data.sf.gov/api/v3/views/c5ge-t6pj/query.json"
ASSESSOR_URL = "https://data.sf.gov/api/v3/views/wv5m-vpq2/query.json"
PAGE_SIZE = 1000
TRANSIENT = {408, 429, 500, 502, 503, 504}
LOT_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
PARCEL_COLUMNS = "the_geom, ludb_id, mapblklot, data_as_of, geography_type"
PARCEL_ORDER = "ORDER BY mapblklot, ludb_id, :id"
PHYSICAL_LOTS = "geography_type = 'parcel' AND mapblklot IS NOT NULL"


class ProviderUnavailable(Exception):
    pass


class DataSF:
    def __init__(self, client: httpx.Client):
        self.client = client

    def page(self, url: str, query: str, number: int, size: int):
        for attempt in range(2):
            try:
                response = self.client.post(url, json={
                    "query": query,
                    "page": {"pageNumber": number, "pageSize": size},
                    "timeout": 2,
                    "includeSynthetic": False,
                    "includeSystem": False,
                })
                if response.status_code in TRANSIENT and attempt == 0:
                    continue
                response.raise_for_status()
                payload = response.json()
            except httpx.TransportError:
                if attempt == 0:
                    continue
                raise ProviderUnavailable("DataSF network request failed") from None
            except (httpx.HTTPStatusError, ValueError):
                raise ProviderUnavailable("DataSF returned a failed or malformed response") from None
            # SODA v3 can wrap rows in a response object. Each row is a keyed record.
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(rows, list) or len(rows) > size or any(not isinstance(row, dict) for row in rows):
                raise ProviderUnavailable("DataSF did not return a bounded list of records")
            return rows

    def paginate(self, url: str, query: str, max_pages: int = 500):
        previous = None
        for number in range(1, max_pages + 1):
            rows = self.page(url, query, number, PAGE_SIZE)
            if rows and rows == previous:
                raise ProviderUnavailable("DataSF repeated a page")
            yield from rows
            if len(rows) < PAGE_SIZE:
                return
            previous = rows
        raise ProviderUnavailable("DataSF pagination exceeded the batch safety limit")


def datasf_client(token: str) -> httpx.Client:
    return httpx.Client(
        headers={"X-App-Token": token},
        timeout=httpx.Timeout(3.0, connect=1.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    )


def parcel_query(bounds) -> str:
    return (
        f"SELECT {PARCEL_COLUMNS} WHERE {PHYSICAL_LOTS} "
        f"AND intersects(the_geom, '{box(*bounds).wkt}') {PARCEL_ORDER}"
    )


def parse_parcel(row) -> ParcelFeature:
    lot = row.get("mapblklot")
    if row.get("geography_type") != "parcel" or not isinstance(lot, str) or LOT_KEY.fullmatch(lot) is None:
        raise ValueError("Invalid DataSF physical lot key")
    geometry = validate_wgs84(shape(row["the_geom"]))
    parcel_id = f"sf:{lot}"
    point = geometry.representative_point()
    return ParcelFeature(
        id=parcel_id,
        geometry=polygon_model(geometry),
        properties=ParcelProperties(
            parcel_id=parcel_id,
            representative_point=Point(coordinates=(point.x, point.y)),
            area_m2=project(geometry).area,
            source_vintage=row.get("data_as_of"),
            quality_flags=["hypothetical_site", "vacancy_unverified", "apns_unverified"],
        ),
    )


def collect_parcels(rows):
    # SoQL orders by physical lot and stable row identifiers across page boundaries.
    for _, group in groupby(rows, key=lambda row: row.get("mapblklot")):
        try:
            parcels = [parse_parcel(row) for row in group]
            first = parcels[0]
            geometry = parcel_shape(first)
            if any(not geometry.equals(parcel_shape(parcel)) for parcel in parcels[1:]):
                raise ValueError("Conflicting geometries for a DataSF physical lot")
        except (KeyError, TypeError, AttributeError, GEOSException, ValueError):
            raise ProviderUnavailable("DataSF returned an invalid physical lot record") from None
        yield first


@dataclass(frozen=True)
class Cached:
    value: object
    retrieved_at: str
    stored: float


class DataSFParcels:
    def __init__(self, api: DataSF, url: str | None = None, version: str | None = None):
        self.api = api
        self.url = url or os.getenv("PARCEL_PLANNER_PARCEL_URL", PARCEL_URL)
        self.version = version or os.getenv("PARCEL_PLANNER_PARCEL_SOURCE_VERSION", "datasf:c5ge-t6pj")
        self._cache = OrderedDict()
        self._lock = Lock()

    def _lookup(self, key):
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                self._cache.move_to_end(key)
            return entry

    def _save(self, key, entry):
        with self._lock:
            self._cache[key] = entry
            self._cache.move_to_end(key)
            if len(self._cache) > 2048:
                self._cache.popitem(last=False)

    def _get(self, key, load):
        entry = self._lookup(key)
        if entry is not None and monotonic() - entry.stored < 60:
            return entry.value, self._source(entry, "cached")
        try:
            value = load()
        except ProviderUnavailable:
            if entry is not None:
                return entry.value, self._source(entry, "stale")
            raise
        entry = Cached(value, datetime.now(timezone.utc).isoformat(), monotonic())
        self._save(key, entry)
        return value, self._source(entry, "live")

    def _source(self, entry, freshness):
        return ParcelSource(version=self.version, retrieved_at=entry.retrieved_at, freshness=freshness)

    def viewport(self, bounds, limit):
        def load():
            rows = self.api.page(self.url, parcel_query(bounds), 1, PAGE_SIZE)
            parcels = list(collect_parcels(rows))
            return parcels[:limit], len(rows) == PAGE_SIZE or len(parcels) > limit

        (parcels, truncated), source = self._get(("bbox", bounds, limit), load)
        if source.freshness == "live":
            for parcel in parcels:
                key = ("parcel", parcel.id)
                existing = self._lookup(key)
                if existing is None or existing.retrieved_at <= source.retrieved_at:
                    self._save(key, Cached(parcel, source.retrieved_at, monotonic()))
        return parcels, truncated, source

    def detail(self, parcel_id):
        lot = parcel_id.removeprefix("sf:")
        if LOT_KEY.fullmatch(lot) is None:
            raise ValueError("Invalid physical lot key")

        def load():
            query = f"SELECT {PARCEL_COLUMNS} WHERE {PHYSICAL_LOTS} AND mapblklot = '{lot}' {PARCEL_ORDER}"
            rows = self.api.page(self.url, query, 1, PAGE_SIZE)
            parcels = list(collect_parcels(rows))
            if len(rows) == PAGE_SIZE or len(parcels) > 1 or any(parcel.id != parcel_id for parcel in parcels):
                raise ProviderUnavailable("DataSF returned an inconsistent physical lot lookup")
            return parcels[0] if parcels else None

        return self._get(("parcel", parcel_id), load)
