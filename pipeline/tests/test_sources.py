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
    current = sf.parse_current_landuse_records(_load("sf_landuse_current_api.json"))
    out, report = sf.build_physical_parcels(parcels, landuse, assessor, "2026-11-01", current_landuse=current)
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
    assert lot["assessed_land_usd"] == 250000  # summed across condo APNs, 2024 row ignored
    assert pd.isna(lot["assessed_improvement_usd"])  # unknown member makes the total unknown
    assert report["condo_lots"] == 1
    assert "sf:0001001" not in out.index  # inactive parcel dropped


@pytest.mark.parametrize("values,expected", [([0, 0], 0), ([100, 200], 300), ([0, None], None), ([None, None], None)])
def test_condo_totals_distinguish_zeros_complete_values_and_unknowns(values, expected):
    parcels = sf.parse_parcel_records(_load("sf_parcels_api.json"))
    assessor = sf.parse_assessor_records(_load("sf_assessor_api.json"), "2025")
    for apn, value in zip(("0025032", "0025033"), values):
        assessor.loc[assessor["apn"] == apn, "assessed_improvement_value_usd"] = value
    lots, _ = sf.build_physical_parcels(parcels, sf.parse_landuse_records(_load("sf_landuse_api.json")), assessor,
                                       "synthetic", current_landuse=sf.parse_current_landuse_records(_load("sf_landuse_current_api.json")))
    actual = lots.set_index("parcel_id").loc["sf:0025032", "assessed_improvement_usd"]
    assert pd.isna(actual) if expected is None else actual == expected


def test_missing_assessor_apn_cannot_produce_a_complete_lot_total():
    parcels = sf.parse_parcel_records(_load("sf_parcels_api.json"))
    assessor = sf.parse_assessor_records(_load("sf_assessor_api.json"), "2025")
    assessor = assessor[assessor["apn"] != "0025033"]
    lots, _ = sf.build_physical_parcels(parcels, sf.parse_landuse_records(_load("sf_landuse_api.json")), assessor,
                                       "synthetic", current_landuse=sf.parse_current_landuse_records(_load("sf_landuse_current_api.json")))
    row = lots.set_index("parcel_id").loc["sf:0025032"]
    assert row["assessor_matched_apns"] == 1
    assert pd.isna(row["assessed_land_usd"]) and pd.isna(row["assessed_improvement_usd"])


def test_vacancy_requires_2023_code_and_no_current_use(lots):
    out, _ = lots
    assert out.loc["sf:0012003A", "vacancy_status"] == "source_indicated_vacant"
    assert out.loc["sf:0012003A", "site_label"] == "source_indicated_vacancy"
    assert out.loc["sf:0025032", "vacancy_status"] == "not_vacant"
    assert out.loc["sf:0025032", "existing_use"] == "residential"
    # No current use but no explicit vacancy code -> unknown; analytical rows ignored.
    assert out.loc["sf:3702001", "vacancy_status"] == "unknown"
    assert out.loc["sf:3702001", "existing_use"] == "no recorded use"
    assert out.loc["sf:3702001", "site_label"] == "hypothetical"
    assert out.loc["sf:9999001", "vacancy_status"] == "unknown"


def test_2023_vacant_lot_developed_since_is_not_vacant():
    parcels = sf.parse_parcel_records(_load("sf_parcels_api.json"))
    landuse = sf.parse_landuse_records(_load("sf_landuse_api.json"))
    current = sf.parse_current_landuse_records([
        {"mapblklot": "0012003A", "ludb_id": "x", "geography_type": "parcel", "resunits": "40"}])
    out, _ = sf.build_physical_parcels(parcels, landuse, sf.parse_assessor_records([], "2025"), None,
                                       current_landuse=current)
    row = out.set_index("parcel_id").loc["sf:0012003A"]
    assert row["vacancy_status"] == "not_vacant" and "developed since" in row["vacancy_evidence"]


def test_geometry_quality_and_representative_point(lots):
    out, _ = lots
    assert out.loc["sf:3702001", "geometry_quality"] == "repaired"  # self-intersecting bowtie
    assert out.loc["sf:9999001", "geometry_quality"] == "unusable"
    assert pd.isna(out.loc["sf:9999001", "rep_lon"])
    v = out.loc["sf:0012003A"]
    assert v["geometry"].contains(__import__("shapely").Point(v["rep_lon"], v["rep_lat"]))
    assert 1500 < v["area_m2"] < 2500  # ~44 m x ~44 m
    assert pd.isna(out.loc["sf:9999001", "assessed_land_usd"])  # missing enrichment stays null


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
    assert df.loc["P4", "closure_status"] == "closed_permanently" and not df.loc["P4", "is_current_supply"]
    assert df.loc["P3", "closure_status"] == "unknown" and df.loc["P3", "is_current_supply"]
    assert df.loc["P1", "closure_status"] == "active"


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


def test_secondary_labels_do_not_readmit_excluded_lookalikes(cats):
    # P4-DATA-005: lookalikes whose primary label is `other` must not keep candidate supply tags.
    assert cats.classify("Convenience store", '["Convenience store","Grocery store"]', "Quick Mart") == ("other", [])
    assert cats.classify("Cardiologist", '["Cardiologist","Doctor"]', "Dr. A, MD") == ("other", [])
    # Mapped primaries keep their secondary candidate services; non-candidate tags still apply.
    assert cats.classify("Grocery store", '["Grocery store","Pharmacy"]', "Safeway") == ("grocery", ["grocery", "pharmacy"])
    assert cats.classify("Non-profit organization", '["Food bank"]', "SF Food Bank") == ("other", ["food_assistance"])


def test_non_physician_credentials_and_colleges_are_excluded(cats):
    for name in ("Jared Lempert, MFT", "Jonah S. Yee, OD", "Geeta A. Davidson, JD", "Pat Chu, LCSW"):
        assert cats.classify("Doctor", "[]", name)[0] == "other", name
    assert cats.classify("Doctor", "[]", "Dr. Pat Kim, MD, PhD")[0] == "clinic"
    assert cats.classify("Religious school", "[]", "Zaytuna College (Upper Campus)")[0] == "other"


def test_submerged_vacant_lot_is_not_a_candidate():
    import geopandas as gpd
    from shapely.geometry import box
    parcels = sf.parse_parcel_records(_load("sf_parcels_api.json"))
    landuse = sf.parse_landuse_records(_load("sf_landuse_api.json"))
    current = sf.parse_current_landuse_records(_load("sf_landuse_current_api.json"))
    assessor = sf.parse_assessor_records(_load("sf_assessor_api.json"), "2025")
    # Water covering all of the vacant lot 0012003A (lon -122.4000..-122.3995, lat 37.7900..37.7904)
    bay = gpd.GeoDataFrame(geometry=[box(-122.4010, 37.7890, -122.3990, 37.7910)], crs="EPSG:4326")
    out, rep = sf.build_physical_parcels(parcels, landuse, assessor, "2026-11-01", current_landuse=current, water=bay)
    lot = out.set_index("parcel_id").loc["sf:0012003A"]
    assert lot["submerged"] and lot["water_fraction"] > 0.99
    assert lot["vacancy_status"] == "unknown" and lot["site_label"] == "hypothetical"
    assert "under water" in lot["vacancy_evidence"]
    assert rep["submerged_lots_reclassified_from_vacant"] == 1
    # Half under water is not submerged: the lot stays vacant
    half = gpd.GeoDataFrame(geometry=[box(-122.4010, 37.7890, -122.39975, 37.7910)], crs="EPSG:4326")
    out2, _ = sf.build_physical_parcels(parcels, landuse, assessor, "2026-11-01", current_landuse=current, water=half)
    lot2 = out2.set_index("parcel_id").loc["sf:0012003A"]
    assert 0.3 < lot2["water_fraction"] < 0.5 and lot2["vacancy_status"] == "source_indicated_vacant"
