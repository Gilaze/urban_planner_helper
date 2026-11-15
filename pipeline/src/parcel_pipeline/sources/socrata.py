"""Bounded, deterministic Socrata (SODA 2.1) client for SF open data."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path

import requests

log = logging.getLogger(__name__)

TRANSIENT = {429, 500, 502, 503, 504}


class SocrataClient:
    def __init__(
        self,
        base_url: str,
        app_token: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        page_size: int = 25_000,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.page_size = page_size
        self.session = session or requests.Session()
        if app_token:
            self.session.headers["X-App-Token"] = app_token

    def _get(self, url: str, params: dict) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("transient error %s; retrying", exc)
            else:
                if resp.status_code not in TRANSIENT or attempt == self.max_retries:
                    resp.raise_for_status()
                    return resp
                log.warning("HTTP %s from %s; retrying", resp.status_code, url)
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def metadata(self, dataset_id: str) -> dict:
        return self._get(f"{self.base_url}/api/views/{dataset_id}.json", {}).json()

    def query(self, dataset_id: str, **soql: str) -> list[dict]:
        params = {f"${k}": v for k, v in soql.items()}
        return self._get(f"{self.base_url}/resource/{dataset_id}.json", params).json()

    def iter_pages(
        self,
        dataset_id: str,
        order: str,
        select: str | None = None,
        where: str | None = None,
        max_rows: int | None = None,
    ) -> Iterator[list[dict]]:
        """Yield pages ordered by a unique key so pagination is deterministic."""
        offset = 0
        while True:
            limit = self.page_size if max_rows is None else min(self.page_size, max_rows - offset)
            if limit <= 0:
                return
            params = {"$order": order, "$limit": str(limit), "$offset": str(offset)}
            if select:
                params["$select"] = select
            if where:
                params["$where"] = where
            page = self._get(f"{self.base_url}/resource/{dataset_id}.json", params).json()
            if not page:
                return
            yield page
            offset += len(page)
            if len(page) < limit:
                return


def snapshot(client: SocrataClient, dataset_id: str, dest: Path, **kw) -> tuple[Path, dict]:
    """Download a dataset into a JSON Lines cache file; return (path, metadata)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = client.metadata(dataset_id)
    tmp = dest.with_suffix(".partial")
    n = 0
    with tmp.open("w") as fh:
        for page in client.iter_pages(dataset_id, **kw):
            for rec in page:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += len(page)
            log.info("%s: %d rows", dataset_id, n)
    tmp.replace(dest)
    return dest, meta


def read_jsonl(path: Path) -> list[dict]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]
