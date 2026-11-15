import copy
import json
from pathlib import Path

import pytest

from .api_checks import validate_collection, validate_comparison

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def comparison():
    return json.loads((FIXTURES / "comparison.json").read_text())


def test_fixture_comparison_uses_independent_ranking(comparison):
    validate_comparison(comparison, {"parcel_id": "sf:checker-0001"})
    assert comparison["active_dimensions"] == ["need", "sustainability"]
    rows = {r["category_id"]: r for r in comparison["alternatives"]}
    # Hand-calculated equal active weights: school=65, clinic=grocery=55,
    # pharmacy=45, bank=15; equal scores break on category ID.
    assert {key: row["rank"] for key, row in rows.items()} == {
        "school": 1, "clinic": 2, "grocery": 3, "pharmacy": 4, "bank_credit_union": 5}


@pytest.mark.parametrize("edit", [
    lambda p: p.update(parcel_id="sf:wrong"),
    lambda p: p.update(scoring_geometry_hash="old-geometry"),
    lambda p: p["versions"].pop("model"),
    lambda p: p["alternatives"][0].update(rank=99),
    lambda p: p["alternatives"][0].update(pareto=False),
    lambda p: p["alternatives"][0].update(total=0),
    lambda p: p["alternatives"][0]["normalized"].update(need=float("nan")),
    lambda p: p["alternatives"][0]["proposal"].update(height_m=100),
    lambda p: p["alternatives"][0]["proposal"].update(geometry=None),
    lambda p: p["alternatives"][-1].update(reason_codes=[]),
    lambda p: p["alternatives"][0]["raw"]["economic"]["retained_tax_usd_per_year"].update(p10=10),
    lambda p: p["alternatives"].pop(),
])
def test_contract_rejects_regressions(comparison, edit):
    edit(comparison)
    with pytest.raises((ValueError, KeyError)):
        validate_comparison(comparison, {"parcel_id": "sf:checker-0001"})


def test_changed_geometry_can_show_details_but_cannot_rank(comparison):
    comparison.update(scoring_available=False, scoring_geometry_hash="old", reason="parcel_geometry_changed")
    for row in comparison["alternatives"]:
        row.update(total=None, rank=None)
    validate_comparison(comparison, {"parcel_id": "sf:checker-0001"})
    comparison["alternatives"][0]["rank"] = 1
    with pytest.raises(ValueError, match="retains scores"):
        validate_comparison(comparison, {"parcel_id": "sf:checker-0001"})


def test_geojson_cap_duplicate_ids_and_closed_services():
    parcel = json.loads((FIXTURES / "provider_records.json").read_text())[0]
    feature = {"type": "Feature", "geometry": parcel["geometry"], "properties": parcel}
    collection = {"type": "FeatureCollection", "features": [feature], "truncated": False, "limit": 1,
                  "source_version": "checker-v1", "freshness": "stale"}
    validate_collection(collection)
    duplicate = copy.deepcopy(collection)
    duplicate.update(limit=2, features=[feature, feature])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_collection(duplicate)
    collection["features"] = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-122.4, 37.7]},
                               "properties": {"place_id": "closed-place", "closure_status": "temporarily_closed"}}]
    with pytest.raises(ValueError, match="Closed"):
        validate_collection(collection, services=True)
