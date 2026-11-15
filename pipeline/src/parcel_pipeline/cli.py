"""Command line entry point: `uv run parcel-pipeline <step>`.

Steps
  fetch-sf       Snapshot SF parcels, land use and assessor roll (Socrata).
  fetch-census   Download TIGER boundaries and ACS estimates (needs CENSUS_API_KEY).
  build          Canonical tables + features into data/artifacts/release/.
  train          Review-activity model, predictions and evaluation report.
  audit          Category audit sample for manual review.
  publish        Copy the backend projection to artifacts/person2 (contracts/README.md).
  all            fetch-sf, fetch-census, build, train, audit.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from . import PIPELINE_VERSION
from .categories import load_categories
from .features import FEATURE_COLUMNS, FEATURE_DEFINITIONS, SupplyIndex, build_parcel_features, build_place_features
from .model import predict_parcels, train_and_evaluate
from .settings import REPO_ROOT, REPORTS_DIR, SPLIT_BY_CITY, Settings, load_settings
from .sources import census, places as places_src, sf
from .sources.socrata import SocrataClient, read_jsonl, snapshot

log = logging.getLogger("parcel_pipeline")


# ------------------------------------------------------------------ helpers
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, (set, tuple, np.ndarray)):
        return list(o)
    raise TypeError(type(o))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=_json_default) + "\n")


def release_dir(s: Settings) -> Path:
    d = s.artifacts_dir / "release"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"sources": {}, "outputs": {}, "reports": {}}

    def source(self, key: str, **info) -> None:
        self.data["sources"][key] = {**self.data["sources"].get(key, {}), **info}

    def output(self, path: Path, rows: int, **info) -> None:
        self.data["outputs"][path.name] = {"rows": int(rows), "sha256": sha256(path), **info}

    def save(self) -> None:
        cats = load_categories()
        self.data["pipeline_version"] = PIPELINE_VERSION
        self.data["category_config_version"] = cats.version
        src_hash = hashlib.sha256(json.dumps(
            {k: v.get("sha256") for k, v in sorted(self.data["sources"].items())}, sort_keys=True).encode()).hexdigest()
        self.data["data_version"] = f"{PIPELINE_VERSION}+{cats.version}+{src_hash[:12]}"
        write_json(self.path, self.data)


# ------------------------------------------------------------------ steps
def fetch_sf(s: Settings, max_rows: int | None = None) -> None:
    client = SocrataClient(s.sf_api_base, s.sf_app_token)
    out = s.raw_dir / "sf"
    m = Manifest(s.raw_dir / "raw_manifest.json")

    years = client.query(sf.ASSESSOR_ID, select="max(closed_roll_year) as y")
    roll_year = years[0]["y"]
    jobs = [
        ("sf_parcels", sf.PARCELS_ID, dict(order="blklot", select=",".join(sf.PARCEL_FIELDS), where="active=true")),
        ("sf_landuse", sf.LANDUSE_ID, dict(order="mapblklot", select=",".join(sf.LANDUSE_FIELDS))),
        ("sf_landuse_current", sf.CURRENT_LANDUSE_ID, dict(order="mapblklot,ludb_id",
                                                           select=",".join(sf.CURRENT_LANDUSE_FIELDS))),
        ("sf_assessor", sf.ASSESSOR_ID, dict(order="parcel_number,row_id", select=",".join(sf.ASSESSOR_FIELDS),
                                             where=f"closed_roll_year='{roll_year}'")),
    ]
    for key, ds, kw in jobs:
        path, meta = snapshot(client, ds, out / f"{key}.jsonl", max_rows=max_rows, **kw)
        n = sum(1 for _ in path.open())
        m.source(key, url=f"{s.sf_api_base}/d/{ds}", dataset_id=ds, name=meta.get("name"), retrieved_at=now(),
                 rows_updated_at=meta.get("rowsUpdatedAt"), rows=n, sha256=sha256(path), path=str(path),
                 query=kw, vintage=roll_year if key == "sf_assessor" else
                 (sf.LANDUSE_VINTAGE if key == "sf_landuse" else meta.get("rowsUpdatedAt")))
        log.info("%s: %d rows", key, n)
    m.data["sources"]["sf_landuse_current"]["vintage"] = max(
        (json.loads(line).get("data_as_of", "") for line in (out / "sf_landuse_current.jsonl").open()),
        default=None)
    m.save()


def fetch_census(s: Settings, skip_acs: bool = False) -> None:
    m = Manifest(s.raw_dir / "raw_manifest.json")
    for level in ("block_group", "tract", "place"):
        gdf_path = census.download(census.TIGER_URLS[level], s.raw_dir / "census" / Path(census.TIGER_URLS[level]).name)
        m.source(f"tiger_{level}", url=census.TIGER_URLS[level], retrieved_at=now(), sha256=sha256(gdf_path),
                 vintage=f"TIGER/Line {census.TIGER_YEAR}", path=str(gdf_path))
    for level in ([] if skip_acs else ["block_group", "tract"]):
        df = census.fetch_acs(level, s.census_api_key)
        path = s.raw_dir / "census" / f"acs_{census.ACS_YEAR}_{level}.csv"
        df.to_csv(path, index=False)
        m.source(f"acs_{level}", url=census.ACS_URL, variables=list(census.ACS_VARS), retrieved_at=now(),
                 rows=len(df), sha256=sha256(path), vintage=f"{census.ACS_YEAR - 4}-{census.ACS_YEAR} ACS 5-year",
                 path=str(path))
    m.save()


def build(s: Settings, skip_census: bool = False) -> None:
    cats = load_categories()
    rd = release_dir(s)
    raw_m = Manifest(s.raw_dir / "raw_manifest.json")
    m = Manifest(rd / "source_manifest.json")
    m.data["sources"] = dict(raw_m.data["sources"])
    m.data["built_at"] = now()
    checks: dict = {}

    # --- Census
    tiger_place = census.load_tiger("place", s.raw_dir)
    cities = census.city_boundaries(tiger_place, tuple(SPLIT_BY_CITY))
    tracts = None
    if skip_census:
        log.warning("--skip-census: demographics and covariates will be null and flagged")
        m.data["census_skipped"] = True
        checks["demographics"] = {"skipped": True}
    else:
        acs_bg = pd.read_csv(s.raw_dir / "census" / f"acs_{census.ACS_YEAR}_block_group.csv", dtype={"geoid": str})
        acs_tr = pd.read_csv(s.raw_dir / "census" / f"acs_{census.ACS_YEAR}_tract.csv", dtype={"geoid": str})
        demo, tracts, demo_rep = census.build_demographics(
            acs_bg, acs_tr, census.load_tiger("block_group", s.raw_dir), census.load_tiger("tract", s.raw_dir))
        p = rd / "demographics.geoparquet"
        demo.to_parquet(p, index=False)
        m.output(p, len(demo), grain="geoid (block group); population for access")
        p = rd / "tract_covariates.geoparquet"
        tracts.to_parquet(p, index=False)
        m.output(p, len(tracts), grain="geoid (tract); model covariates")
        checks["demographics"] = demo_rep
        m.data["census_skipped"] = False

    # --- SF parcels
    parcel_records = read_jsonl(s.raw_dir / "sf" / "sf_parcels.jsonl")
    parcels_raw = sf.parse_parcel_records(parcel_records)
    landuse = sf.parse_landuse_records(read_jsonl(s.raw_dir / "sf" / "sf_landuse.jsonl"))
    roll_year = raw_m.data["sources"].get("sf_assessor", {}).get("vintage")
    assessor = sf.parse_assessor_records(read_jsonl(s.raw_dir / "sf" / "sf_assessor.jsonl"), roll_year)
    parcel_vintage = parcels_raw["data_as_of"].dropna().max() if parcels_raw["data_as_of"].notna().any() else None
    current_lu = sf.parse_current_landuse_records(read_jsonl(s.raw_dir / "sf" / "sf_landuse_current.jsonl"))
    lots, lot_rep = sf.build_physical_parcels(parcels_raw, landuse, assessor, parcel_vintage,
                                              current_landuse=current_lu)
    p = rd / "parcels.geoparquet"
    lots.to_parquet(p, index=False)
    m.output(p, len(lots), grain="physical lot (mapblklot)", key="parcel_id")
    checks["parcels"] = {**lot_rep, "api_records": len(parcel_records),
                         "records_missing_blklot_or_mapblklot": len(parcel_records) - len(parcels_raw)}
    del parcel_records

    # --- Places + reviews
    if not s.places_csv or not s.reviews_csv:
        raise SystemExit("PLACES_CSV and REVIEWS_CSV must point at the Bright Data exports")
    for key, path, url in (("google_places", s.places_csv, "Bright Data Google Maps Places (Bay Area)"),
                           ("google_reviews", s.reviews_csv, "Bright Data Google Maps Reviews (Bay Area sample)")):
        m.source(key, url=url, path=str(path), sha256=sha256(path), retrieved_at=now(), vintage="hackathon export")
    pl, pl_rep = places_src.read_places(s.places_csv, cats)
    pl = places_src.finalize_places(places_src.assign_city(pl, cities))
    p = rd / "places.parquet"
    pl.to_parquet(p, index=False)
    m.output(p, len(pl), grain="place_id", key="place_id")
    m.source("google_places", rows=pl_rep["input_rows"])

    summary, rv_rep = places_src.aggregate_reviews(s.reviews_csv, set(pl["place_id"]))
    rs = places_src.review_summary_for_places(pl, summary)
    p = rd / "review_summary.parquet"
    rs.to_parquet(p, index=False)
    m.output(p, len(rs), grain="place_id", key="place_id")
    m.source("google_reviews", rows=rv_rep["review_rows"])
    checks["places"] = {**pl_rep, **places_coverage(pl, rs)}
    checks["reviews"] = rv_rep

    # --- Features
    pl_xy = pl.rename(columns={"longitude": "lon", "latitude": "lat"})
    index = SupplyIndex(pl_xy, cats.candidates)
    pf = build_place_features(pl_xy, index, tracts, SPLIT_BY_CITY)
    p = rd / "place_features.parquet"
    pf.to_parquet(p, index=False)
    m.output(p, len(pf), grain="place_id", feature_columns=FEATURE_COLUMNS)
    qf = build_parcel_features(lots, index, tracts)
    p = rd / "parcel_features.parquet"
    qf.to_parquet(p, index=False)
    m.output(p, len(qf), grain="parcel_id x category", feature_columns=FEATURE_COLUMNS)
    write_json(rd / "feature_definitions.json", {"columns": FEATURE_COLUMNS, "definitions": FEATURE_DEFINITIONS,
                                                  "categories": list(cats.candidates), "crs": "EPSG:26910"})
    checks["features"] = {
        "place_rows_by_split_category": pf.groupby(["split", "category"]).size().unstack(fill_value=0).to_dict(),
        "place_labeled_rows": int(pf["labeled"].sum()),
        "parcel_rows": int(len(qf)),
        "parcels_without_rep_point": int(lots["rep_lon"].isna().sum()),
        "covariate_missing_rate_parcels": {c: float(qf[c].isna().mean()) for c in
                                           ("population_density_per_km2", "median_household_income_usd",
                                            "median_gross_rent_usd_month")},
    }
    m.data["reports"]["data_checks"] = "reports/data/data_checks.json"
    write_json(REPORTS_DIR / "data_checks.json", checks)
    write_checkpoint_md(checks, m)
    m.save()


def places_coverage(pl: pd.DataFrame, rs: pd.DataFrame) -> dict:
    city = pl["city"].fillna("outside split cities")
    tags = pl.explode("service_tags").reset_index(drop=True)
    return {
        "closure_status": pl["closure_status"].value_counts().to_dict(),
        "primary_by_city": pd.crosstab(pl["primary_category"], city).to_dict(),
        "current_supply_tags_by_city": pd.crosstab(
            tags.loc[tags["is_current_supply"], "service_tags"],
            tags.loc[tags["is_current_supply"], "city"].fillna("outside split cities")).to_dict(),
        "reviews_count_missing": int(pl["reviews_count"].isna().sum()),
        "reviews_count_zero": int((pl["reviews_count"] == 0).sum()),
        "address_city_vs_spatial_city_disagree": int(
            (pl["city"].notna() & pl["address_city"].notna() & (pl["city"] != pl["address_city"])).sum()),
        "places_with_sampled_reviews": int(rs["has_sampled_reviews"].sum()),
    }


def train(s: Settings) -> None:
    cats = load_categories()
    rd = release_dir(s)
    m = Manifest(rd / "source_manifest.json")
    pf = pd.read_parquet(rd / "place_features.parquet")
    qf = pd.read_parquet(rd / "parcel_features.parquet")
    result = train_and_evaluate(pf, list(cats.candidates))
    rep = result["report"]
    rep["data_version"] = m.data.get("data_version")
    rep["census_skipped"] = m.data.get("census_skipped")

    preds = predict_parcels(qf, result)
    model_version = f"review-activity/{result['selected']}/{PIPELINE_VERSION}"
    preds["model_version"] = model_version
    p = rd / "review_predictions.parquet"
    preds.to_parquet(p, index=False)
    m.output(p, len(preds), grain="parcel_id x category")
    p = rd / "place_eval_predictions.parquet"
    result["eval_predictions"].to_parquet(p, index=False)
    m.output(p, len(result["eval_predictions"]), grain="place_id (validation + test)")
    p = rd / "splits.parquet"
    pf[["place_id", "category", "city", "split", "labeled"]].to_parquet(p, index=False)
    m.output(p, len(pf), grain="place_id")
    if result["final_model"] is not None:
        import joblib
        mp = rd / "review_activity_model.joblib"
        joblib.dump(result["final_model"], mp)
        m.output(mp, 1, note="binary; gitignored")
    write_json(rd / "model_report.json", rep)
    write_json(REPORTS_DIR / "model_report.json", rep)
    write_model_md(rep)
    m.data["model"] = {"version": model_version, "selected": result["selected"], "gate_passed": rep["gate"]["passed"],
                       "model_categories": result["model_categories"], "report": "reports/data/model_report.json"}
    m.save()
    write_backend_manifest(rd, m)


def write_backend_manifest(rd: Path, m: Manifest) -> dict:
    """Serving descriptor for backend/build_release.py (see contracts/README.md)."""
    src = m.data["sources"]
    parcels = src.get("sf_parcels", {})
    lots = pd.read_parquet(rd / "parcels.geoparquet", columns=["parcel_id", "vacancy_status", "geometry_quality"])
    usable = lots[_publishable(lots)]
    hypothetical = None
    if not (usable["vacancy_status"] == "source_indicated_vacant").any():
        hypothetical = str(usable["parcel_id"].min())  # stable choice among verified geometries
    spatial = None
    demo_path = rd / "demographics.geoparquet"
    if demo_path.exists():
        demo = gpd.read_parquet(demo_path, columns=["geoid", "geometry"])
        # Block groups for the five retained counties = jointly covered by ACS and
        # the Places export (whose bounding box contains all five).
        cover = demo.to_crs("EPSG:26910").geometry.union_all().buffer(0).simplify(25)
        spatial = json.loads(json.dumps(gpd.GeoSeries([cover], crs="EPSG:26910").to_crs(4326).iloc[0].__geo_interface__))
    model = m.data.get("model", {})
    desc = {
        "data_version": m.data.get("data_version"),
        "model_version": model.get("version"),
        "parcel_source_version": f"datasf:{parcels.get('dataset_id', sf.PARCELS_ID)}@{parcels.get('rows_updated_at')}",
        "parcel_retrieved_at": parcels.get("retrieved_at"),
        "sources": {k: str(v.get("url")) for k, v in sorted(src.items()) if v.get("url")},
        "coverage": {
            "physical_lots": int(len(lots)),
            "published_lots": int(len(usable)),
            "lots_excluded_unusable_geometry": int((lots["geometry_quality"] == "unusable").sum()),
            "lots_excluded_id_outside_contract": int((~lots["parcel_id"].str.match(CONTRACT_PARCEL_ID)).sum()),
            "source_indicated_vacant_lots": int((usable["vacancy_status"] == "source_indicated_vacant").sum()),
            "unknown_vacancy_lots": int((usable["vacancy_status"] == "unknown").sum()),
            "landuse_vintage": f"current {src.get('sf_landuse_current', {}).get('vintage')}; vacancy code {sf.LANDUSE_VINTAGE}",
            "assessor_roll_year": src.get("sf_assessor", {}).get("vintage"),
            "census": "skipped" if m.data.get("census_skipped") else "ACS 2020-2024 5-year block groups",
            "census_counties": "06001,06013,06041,06075,06081",
            "review_model_selected": model.get("selected"),
            "category_config_version": m.data.get("category_config_version"),
        },
        "spatial_coverage": spatial,
        "hypothetical_parcel_id": hypothetical,
    }
    write_json(rd / "backend_manifest.json", desc)
    return desc


# backend/schemas.py ParcelID pattern. SF has at least one lot key with "*"
# (4591A011*), which the API contract cannot represent.
CONTRACT_PARCEL_ID = r"^sf:[A-Za-z0-9_-]{1,64}$"


def _publishable(lots: pd.DataFrame) -> pd.Series:
    return (lots["geometry_quality"] != "unusable") & lots["parcel_id"].str.match(CONTRACT_PARCEL_ID)


BACKEND_FILES = ["backend_manifest.json", "parcels.geoparquet", "places.parquet", "demographics.geoparquet",
                 "review_predictions.parquet", "source_manifest.json"]


def publish(s: Settings, out: Path, allow_missing_census: bool = False) -> None:
    """Copy the backend projection of the release to `out` (default <repo>/artifacts/person2)."""
    import shutil
    from tempfile import TemporaryDirectory
    import pyarrow.parquet as pq
    rd = release_dir(s)
    if out.resolve() == rd.resolve():
        raise ValueError("Publish destination must differ from the canonical release directory")
    missing = [f for f in BACKEND_FILES if not (rd / f).exists()]
    if missing and allow_missing_census and missing == ["demographics.geoparquet"]:
        log.warning("publishing WITHOUT demographics.geoparquet; backend_manifest records census=skipped")
        missing = []
    if missing:
        raise SystemExit(f"release is missing {missing}; run build (with Census) and train first")
    canonical_path = rd / "source_manifest.json"
    canonical = json.loads(canonical_path.read_text())
    canonical_outputs = canonical.get("outputs", {})
    files = [name for name in BACKEND_FILES if name != "source_manifest.json" and (rd / name).exists()]
    for name in files:
        meta = canonical_outputs.get(name, {})
        if "sha256" in meta and meta["sha256"] != sha256(rd / name):
            raise ValueError(f"Canonical fingerprint mismatch for {name}; rebuild or restore the release")
        if name.endswith((".parquet", ".geoparquet")) and "rows" in meta and meta["rows"] != pq.ParquetFile(rd / name).metadata.num_rows:
            raise ValueError(f"Canonical row count mismatch for {name}")
    lots = gpd.read_parquet(rd / "parcels.geoparquet")
    keep = _publishable(lots)
    kept_ids = set(lots.loc[keep, "parcel_id"])
    preds = pd.read_parquet(rd / "review_predictions.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="parcel-publish-", dir=out.parent) as temporary:
        staged = Path(temporary)
        lots[keep].to_parquet(staged / "parcels.geoparquet", index=False)
        preds[preds["parcel_id"].isin(kept_ids)].to_parquet(staged / "review_predictions.parquet", index=False)
        for name in files:
            if name not in ("parcels.geoparquet", "review_predictions.parquet"):
                shutil.copy2(rd / name, staged / name)
        if "demographics.geoparquet" not in files:
            descriptor = json.loads((staged / "backend_manifest.json").read_text())
            descriptor.setdefault("coverage", {})["census"] = "skipped"
            descriptor["spatial_coverage"] = None
            write_json(staged / "backend_manifest.json", descriptor)

        published = copy.deepcopy(canonical)
        published["outputs"] = {}
        for name in files:
            meta = copy.deepcopy(canonical_outputs.get(name, {}))
            meta["sha256"] = sha256(staged / name)
            if name.endswith((".parquet", ".geoparquet")):
                meta["rows"] = pq.ParquetFile(staged / name).metadata.num_rows
            published["outputs"][name] = meta
        published["census_skipped"] = "demographics.geoparquet" not in files
        published["publication"] = {
            "format": "backend-serving-projection-v1", "publisher_pipeline_version": PIPELINE_VERSION,
            "canonical_manifest_sha256": sha256(canonical_path), "canonical_data_version": canonical.get("data_version"),
            "canonical_outputs": copy.deepcopy(canonical_outputs),
            "omitted_files": sorted(set(canonical_outputs) - set(files)),
            "omitted_parcels": {"total": int((~keep).sum()),
                                "unusable_geometry": int((lots["geometry_quality"] == "unusable").sum()),
                                "id_outside_contract": int((~lots["parcel_id"].str.match(CONTRACT_PARCEL_ID)).sum()),
                                "reason_counts_can_overlap": True},
        }
        write_json(staged / "source_manifest.json", published)
        out.mkdir(parents=True, exist_ok=True)
        for name in files:
            (staged / name).replace(out / name)
        if "demographics.geoparquet" not in files:
            (out / "demographics.geoparquet").unlink(missing_ok=True)
        # Publish the manifest only after all bytes it fingerprints are in place.
        (staged / "source_manifest.json").replace(out / "source_manifest.json")
    log.info("published %d files to %s (%d lots omitted: unusable geometry or ID outside contract)",
             len(files) + 1, out, int((~keep).sum()))


def audit(s: Settings, n: int = 20, seed: int = 42) -> None:
    cats = load_categories()
    pl = pd.read_parquet(release_dir(s) / "places.parquet")
    pool = pl[pl["city"].notna()]
    frames = []
    for c in cats.candidates:
        g = pool[pool["primary_category"] == c]
        frames.append(g.sample(min(n, len(g)), random_state=seed).assign(audit_reason=f"candidate:{c}"))
    lookalike = pool["source_category"].str.contains(r"clinic|grocery|market|supermarket|school|bank|pharmac",
                                                      case=False, na=False) & (pool["primary_category"] == "other")
    excluded = pool.apply(lambda r: any(cats.excluded(g, r["name"]) for g in cats.candidates
                                        if cats.group_of(r["source_category"]) == g), axis=1)
    amb = pool[lookalike | excluded]
    frames.append(amb.sample(min(n, len(amb)), random_state=seed).assign(audit_reason="ambiguous"))
    for c in ("clinic", "grocery"):
        g = pool[(pool["primary_category"] == "other") & pool["service_tags"].apply(lambda t, c=c: c in list(t))]
        frames.append(g.sample(min(n, len(g)), random_state=seed).assign(audit_reason=f"lookalike:{c}"))
    out = pd.concat(frames)[["audit_reason", "place_id", "name", "city", "source_category", "primary_category",
                             "service_tags"]]
    out["service_tags"] = out["service_tags"].apply(lambda t: "|".join(t))
    out["reviewer_verdict"] = ""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(REPORTS_DIR / "category_audit_sample.csv", index=False)
    log.info("wrote %d audit rows", len(out))


# ------------------------------------------------------------------ reports
def write_checkpoint_md(checks: dict, m: Manifest) -> None:
    lines = ["# Data checkpoint", "", f"Built: {m.data.get('built_at')}", "",
             "## Sources", "", "| Source | Vintage | Rows | Retrieved |", "|---|---|---|---|"]
    for k, v in sorted(m.data["sources"].items()):
        lines.append(f"| {k} | {v.get('vintage', '')} | {v.get('rows', '')} | {v.get('retrieved_at', '')} |")
    p = checks["parcels"]
    lines += ["", "## Parcels", "",
              f"- Grain: {p['grain']}",
              f"- API records {p['api_records']:,}; dropped for missing blklot/mapblklot "
              f"{p['records_missing_blklot_or_mapblklot']:,}",
              f"- Active APNs {p['active_apns']:,} -> physical lots {p['physical_lots']:,} ({p['condo_lots']:,} multi-APN)",
              f"- Geometry quality: {p['geometry_quality']}",
              f"- Land use matched lots: {p['landuse_matched_lots']:,}; land-use rows without an active lot: {p['landuse_unmatched_rows']:,}",
              f"- Vacancy status: {p['vacancy_status']}",
              f"- Vacancy evidence: {p['vacancy_evidence']}",
              f"- Current land use (c5ge-t6pj) matched lots: {p['current_landuse_matched_lots']:,}",
              f"- Assessor roll {p['assessor_roll_years']}: matched APNs {p['assessor_matched_apns']:,}, "
              f"unmatched active APNs {p['assessor_unmatched_active_apns']:,}, roll rows without active APN "
              f"{p['assessor_rows_without_active_apn']:,}",
              "", "## Places and reviews", ""]
    pc, rv = checks["places"], checks["reviews"]
    lines += [f"- Input rows {pc['input_rows']:,}; rejected {pc['rejected']}; duplicates removed {pc['duplicates_removed']}",
              f"- Closure status: {pc['closure_status']}",
              f"- reviews_count missing {pc['reviews_count_missing']:,}; zero {pc['reviews_count_zero']:,}",
              f"- Address city vs spatial city disagreements: {pc['address_city_vs_spatial_city_disagree']:,}",
              f"- Reviews: {rv['review_rows']:,} rows over {rv['reviewed_place_ids']:,} place IDs; "
              f"matched {rv['matched_place_ids']:,}, unmatched {rv['unmatched_place_ids']:,}; "
              f"invalid ratings {rv['invalid_rating_rows']:,}",
              "", "### Primary category by city", "", _crosstab_md(pc["primary_by_city"]),
              "", "## Features", "", "```json", json.dumps(checks["features"], indent=2, default=_json_default), "```"]
    (REPORTS_DIR / "data_checkpoint.md").write_text("\n".join(lines) + "\n")


def _crosstab_md(ct: dict) -> str:
    df = pd.DataFrame(ct).fillna(0).astype(int)
    cols = list(df.columns)
    out = ["| category | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for idx, row in df.iterrows():
        out.append(f"| {idx} | " + " | ".join(f"{v:,}" for v in row) + " |")
    return "\n".join(out)


def write_model_md(rep: dict) -> None:
    def f(x):
        return "—" if x is None else f"{x:.3f}"
    g = rep["gate"]
    lines = ["# Review-activity model report", "",
             f"Data version: `{rep.get('data_version')}`  ", f"Estimator: {rep['estimator']}  ",
             f"Census covariates skipped: {rep.get('census_skipped')}", "",
             "## Labeled sample sizes", "", "| category | Oakland (train) | Berkeley (validation) | SF (test) |",
             "|---|---|---|---|"]
    for c, n in rep["labeled_counts"].items():
        lines.append(f"| {c} | {n['train']} | {n['validation']} | {n['test']} |")
    lines += ["", f"Model categories (≥100 Oakland, ≥30 Berkeley): {rep['model_categories']}  ",
              f"Unsupported: {rep['unsupported_categories']}  ",
              f"Degenerate target (all Oakland training counts are 0, treated as unlabeled): "
              f"{rep.get('degenerate_target_categories', [])}", "",
              "## Selection (Berkeley)", "", "| params | macro RMSE |", "|---|---|"]
    for e in rep["validation"]["candidates"]:
        lines.append(f"| {e['params']} | {f(e['macro_rmse'])} |")
    lines += [f"| category baseline | {f(g['category_baseline_macro_rmse'])} |", "",
              f"Gate (≥5% better than category baseline): **{'passed' if g['passed'] else 'not passed'}**"
              f" — relative improvement {f(g['relative_improvement'])}. Selected: **{rep['selected']}**.", "",
              "## SF test (evaluated once, refit on Oakland + Berkeley)", "",
              "| category | n | selected RMSE | selected Spearman | category baseline RMSE | city×category baseline RMSE |",
              "|---|---|---|---|---|---|"]
    t = rep["test"]
    for c, s in t["selected"].items():
        b, bc = t["category_baseline"][c], t["category_city_baseline"][c]
        lines.append(f"| {c} | {s['n']} | {f(s['rmse_log_count'])} | {f(s['spearman'])} | "
                     f"{f(b['rmse_log_count'])} | {f(bc['rmse_log_count'])} |")
    lines += ["", "## Slices", "", "### Berkeley", "", _slices_md(rep["validation"]["slices"]),
              "", "### SF", "", _slices_md(t["slices"]), "",
              "## Representative failures (SF, largest absolute log-count errors)", "",
              "| place_id | category | actual | predicted |", "|---|---|---|---|"]
    for r in t["worst_errors"]:
        lines.append(f"| {r['place_id']} | {r['category']} | {f(r['target_log1p_reviews_count'])} | {f(r['pred_selected'])} |")
    lines += ["", "The prediction is a review-activity indicator (log1p of Google review count), "
              "not a measure of demand, quality or need."]
    (REPORTS_DIR / "model_report.md").write_text("\n".join(lines) + "\n")


def _slices_md(sl: dict) -> str:
    out = ["| slice | n | RMSE | mean within-category Spearman |", "|---|---|---|---|"]
    for k, v in sl.items():
        if str(v.get("status", "")).startswith("unavailable"):
            out.append(f"| {k} | — | {v['status']} | |")
        elif v.get("status") == "insufficient":
            out.append(f"| {k} | {v['n']} | insufficient (<30) | |")
        elif "n" in v:
            r, sp = v.get("rmse_log_count"), v.get("mean_within_category_spearman")
            out.append(f"| {k} | {v['n']} | {'—' if r is None else f'{r:.3f}'} | {'undefined' if sp is None else f'{sp:.3f}'} |")
    return "\n".join(out)


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parcel-pipeline")
    ap.add_argument("step", choices=["fetch-sf", "fetch-census", "build", "train", "audit", "publish", "all"])
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "artifacts" / "person2",
                    help="publish destination for backend/build_release.py --input")
    ap.add_argument("--skip-census", action="store_true", help="build without ACS covariates (flagged in manifest)")
    ap.add_argument("--max-rows", type=int, default=None, help="cap rows per SF dataset (smoke tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    steps = {
        "fetch-sf": lambda: fetch_sf(s, args.max_rows),
        "fetch-census": lambda: fetch_census(s, args.skip_census),
        "build": lambda: build(s, args.skip_census),
        "train": lambda: train(s),
        "audit": lambda: audit(s),
        "publish": lambda: publish(s, args.out, args.skip_census),
    }
    order = ["fetch-sf", "fetch-census", "build", "train", "audit"]
    for name in (order if args.step == "all" else [args.step]):
        log.info("== %s", name)
        steps[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
