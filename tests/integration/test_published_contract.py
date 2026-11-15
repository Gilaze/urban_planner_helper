from pathlib import Path

import pytest

from .artifacts import read_json
from .oracle import DIMENSIONS, empirical_percentile, need, sustainability
from .published_api import fixture_cases, validate_comparison

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"


def published():
    if not (CONTRACTS / "examples/compare.json").exists():
        pytest.skip("Backend has not published its contract on this checkout")
    return read_json(CONTRACTS / "examples/compare.json")


def test_published_comparison_matches_independent_ranking():
    data = published()
    validate_comparison(data, {"parcel_id": "sf:fixture-001"})


@pytest.mark.parametrize("category", ["clinic", "pharmacy", "grocery", "bank_credit_union", "school"])
def test_published_raw_metrics_match_independently_projected_source(category):
    data = published()
    case = next(c for c in fixture_cases(CONTRACTS / "fixture-inputs.json") if c["category_id"] == category)
    expected_need = need(case["populations"], case["existing_distances_m"], case["proposed_distances_m"])
    expected_travel = sustainability(case["populations"], case["baseline_distances_m"], case["proposed_distances_m"])
    row = next(r for r in data["alternatives"] if r["category_id"] == category)
    assert row["need"]["deficit_before"] == pytest.approx(expected_need["deficit_before"])
    assert row["need"]["deficit_after"] == pytest.approx(expected_need["deficit_after"])
    assert row["need"]["deficit_reduction"] == pytest.approx(expected_need["benefit"])
    assert row["sustainability"]["distance_before_m"] == pytest.approx(expected_travel["before_m"])
    assert row["sustainability"]["distance_after_m"] == pytest.approx(expected_travel["after_m"])
    assert row["sustainability"]["avoided_vehicle_miles_per_day"] == pytest.approx(expected_travel["avoided_vehicle_miles_per_day"])


def test_published_fixture_percentiles_use_same_frozen_universe_and_midpoint_ties():
    data = published()
    fields = {"need": lambda r: r["need"]["deficit_reduction"],
              "retained_sales_tax": lambda r: (r["economic"]["retained_sales_tax_usd_per_year"] or {}).get("p50"),
              "operating_fte": lambda r: (r["economic"]["operating_fte"] or {}).get("p50"),
              "sustainability": lambda r: r["sustainability"]["avoided_vehicle_miles_per_day"]["base"]}
    for name, raw in fields.items():
        reference = [raw(row) for row in data["alternatives"] if raw(row) is not None]
        for row in data["alternatives"]:
            expected = empirical_percentile(raw(row), reference) if raw(row) is not None else None
            assert row["normalized"][name] == expected


def test_published_validator_rejects_changed_ranks():
    data = published()
    data["alternatives"][0]["rank"] = 99
    with pytest.raises(ValueError, match="Rank/Pareto"):
        validate_comparison(data, {"parcel_id": "sf:fixture-001"})
