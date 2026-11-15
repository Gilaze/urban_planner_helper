import json
from pathlib import Path

import pandas as pd
import pytest

from parcel_pipeline.categories import load_categories
from parcel_pipeline.sources import places as P
from parcel_pipeline.sources import sf

FX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FX / name).read_text())


@pytest.fixture(scope="module")
def cats():
    return load_categories()


@pytest.fixture(scope="module")
def lots():
    parcels = sf.parse_parcel_records(_load("sf_parcels_api.json"))
    landuse = sf.parse_landuse_records(_load("sf_landuse_api.json"))
    assessor = sf.parse_assessor_records(_load("sf_assessor_api.json"), "2025")
    out, report = sf.build_physical_parcels(parcels, landuse, assessor, "2026-11-01")
    return out.set_index("parcel_id"), report


# ---------------------------------------------------------------- categories
def test_category_config_has_15_groups_and_candidates(cats):
    assert len(cats.groups) == 15
    assert cats.candidates == ("clinic", "pharmacy", "grocery", "bank_credit_union", "school")


def test_classify_primary_tags_and_exclusions(cats):
    assert cats.classify("Medical clinic", '["Medical clinic","Pharmacy"]', "Main St Clinic") == (
        "clinic", ["clinic", "pharmacy"])
    # Med-spa lookalike carrying a legitimate label is excluded from clinic entirely.
    assert cats.classify("Medical clinic", '["Medical clinic"]', "Glow Med Spa") == ("other", [])
    # Convenience stores are not grocery supply.
    assert cats.classify("Convenience store", '["Convenience store"]', "Quick Mart") == ("other", [])
    assert cats.classify("Dog day care center", "[]", "Doggo")[0] == "other"
    assert cats.classify("  grocery   STORE ", None, "x")[0] == "grocery"


# ---------------------------------------------------------------- SF parcels
def test_parcel_parsing_preserves_string_apns_and_drops_bad_keys():
    df = sf.parse_parcel_records(_load("sf_parcels_api.json"))
    assert "0012003A" in set(df["blklot"])  # leading zero and letter suffix preserved
    assert df["blklot"].notna().all() and len(df) == 6


def test_condo_apns_consolidate_to_one_physical_lot(lots):
    out, report = lots
    lot = out.loc["sf:0025032"]
    assert list(lot["source_apns"]) == ["0025032", "0025033"]
    assert lot["apn_count"] == 2
    assert lot["assessed_land_value_usd"] == 250000  # summed across condo APNs, 2024 row ignored
    assert lot["assessed_improvement_value_usd"] == 200000  # null member does not null the sum
    assert report["condo_lots"] == 1
    assert "sf:0001001" not in out.index  # inactive parcel dropped


def test_vacancy_status_uses_explicit_code_only(lots):
    out, _ = lots
    assert out.loc["sf:0012003A", "vacancy_status"] == "source_indicated_vacant"
    assert out.loc["sf:0012003A", "site_label"] == "source_indicated_vacancy"
    assert out.loc["sf:0025032", "vacancy_status"] == "not_vacant"  # duplicate null land-use row ignored
    assert out.loc["sf:3702001", "vacancy_status"] == "unknown"
    assert out.loc["sf:3702001", "site_label"] == "hypothetical"


def test_geometry_quality_and_representative_point(lots):
    out, _ = lots
    assert out.loc["sf:3702001", "geometry_quality"] == "repaired"  # self-intersecting bowtie
    assert out.loc["sf:9999001", "geometry_quality"] == "unusable"
    assert pd.isna(out.loc["sf:9999001", "rep_lon"])
    v = out.loc["sf:0012003A"]
    assert v["geometry"].contains(__import__("shapely").Point(v["rep_lon"], v["rep_lat"]))
    assert 1500 < v["area_m2"] < 2500  # ~44 m x ~44 m
    assert pd.isna(out.loc["sf:9999001", "assessed_land_value_usd"])  # missing enrichment stays null


def test_join_report(lots):
    _, r = lots
    assert r["landuse_unmatched_rows"] == 1  # 8888001
    assert r["assessor_rows_without_active_apn"] == 1  # 7777001


# ---------------------------------------------------------------- Places
@pytest.fixture(scope="module")
def places(cats):
    df, report = P.read_places(FX / "places.csv", cats, chunksize=3)
    return df.set_index("place_id"), report


def test_places_validation(places):
    df, r = places
    assert r["rejected"]["missing_place_id"] == 1
    assert r["rejected"]["invalid_coordinates"] == 1
    assert "P5" not in df.index
    assert pd.isna(df.loc["P7", "reviews_count"])  # negative count -> unlabeled


def test_closure_status(places):
    df, _ = places
    assert df.loc["P4", "closure_status"] == "permanently_closed" and not df.loc["P4", "is_current_supply"]
    assert df.loc["P3", "closure_status"] == "unknown" and df.loc["P3", "is_current_supply"]
    assert df.loc["P1", "closure_status"] == "open"


def test_places_dedupe_prefers_complete_then_source_order():
    df = pd.DataFrame({"place_id": ["A", "A", "A"], "_completeness": [3, 5, 5], "_source_row": [0, 2, 1]})
    assert P.dedupe_places(df)["_source_row"].tolist() == [1]


def test_reviews_aggregate_without_identity(places):
    df, _ = places
    s, r = P.aggregate_reviews(FX / "reviews.csv", set(df.index))
    assert "reviewer_name" not in s.columns and "review" not in s.columns
    p1 = s.set_index("place_id").loc["P1"]
    assert p1["sampled_review_count"] == 3 and p1["valid_rating_count"] == 2 and p1["mean_rating"] == 4.0
    assert r["unmatched_place_ids"] == 1 and r["invalid_rating_rows"] == 1
    out = P.review_summary_for_places(df.reset_index(), s)
    assert len(out) == len(df)  # Places rows kept without reviews
    assert out.set_index("place_id").loc["P6", "sampled_review_count"] == 0
