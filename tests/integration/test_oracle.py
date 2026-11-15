import math

import pytest

from .oracle import (CANDIDATES, accessibility, distance_weight, economic_scenarios,
                     empirical_percentile, need, rank, sustainability)


@pytest.mark.parametrize("distance,expected", [(0, 1), (400, 1), (400.001, .5), (800, .5), (800.001, 0)])
def test_catchment_boundaries(distance, expected):
    assert distance_weight(distance) == expected


def test_two_population_hand_calculation():
    # Existing: weights (1, .5), denominator 100 + 200*.5 = 200.
    # New: weights (.5, 1), denominator 100*.5 + 200 = 250.
    # A_before=(1/200, 1/400); A_after=(7/1000, 13/2000).
    result = need([100, 200], [[400], [800]], [800, 400])
    assert result["before"]["facility_ratios"] == pytest.approx([1 / 200])
    assert result["after"]["facility_ratios"] == pytest.approx([1 / 200, 1 / 250])
    assert result["before"]["accessibility"] == pytest.approx([7 / 1400, 1 / 400])
    assert result["after"]["accessibility"] == pytest.approx([7 / 1000, 13 / 2000])
    assert result["target"] == pytest.approx(3 / 800)
    assert result["deficit_before"] == pytest.approx(2 / 9)
    assert result["deficit_after"] == 0
    assert result["benefit"] == pytest.approx(2 / 9)
    assert result["covered_population"] == 300


def test_frozen_target_and_local_scope_include_population_outside_scope_for_ratios():
    result = need([100, 100], [[0], [0]], [0, 801], target=.02)
    assert result["target"] == .02
    assert result["covered_population"] == 100
    assert result["deficit_before"] == pytest.approx(.75)
    assert result["deficit_after"] == pytest.approx(.25)


def test_no_supply_is_unavailable_instead_of_inventing_a_target():
    result = need([100, 200], [[], []], [10, 20])
    assert result["benefit"] is None
    assert result["reason"] == "no_positive_baseline"


def test_zero_population_and_zero_denominator():
    result = need([0, 0], [[0], [600]], [0, 0], target=.1)
    assert result["before"]["zero_population_facilities"] == [0]
    assert result["after"]["zero_population_facilities"] == [0, 1]
    assert result["benefit"] is None
    assert result["reason"] == "zero_population"


@pytest.mark.parametrize("populations", [[1, 1], [0, 100], [100, 200]])
@pytest.mark.parametrize("proposal", [[0, 400], [400, 800], [800, 801]])
def test_supply_cannot_reduce_accessibility(populations, proposal):
    result = need(populations, [[0, 700], [500, 800]], proposal)
    assert all(a >= b for a, b in zip(result["after"]["accessibility"], result["before"]["accessibility"]))
    assert result["benefit"] is None or result["benefit"] >= 0


def test_travel_hand_calculation_and_paired_sensitivity():
    # 100*(1000-200) + 200*(600-400) = 120000 person-meters.
    result = sustainability([100, 200], [1000, 600], [200, 400])
    assert result["before_m"] == pytest.approx(2200 / 3)
    assert result["after_m"] == pytest.approx(1000 / 3)
    assert result["saved_m"] == pytest.approx(400)
    assert result["baseline_coverage"] == 1
    assert result["avoided_vehicle_miles_per_day"] == pytest.approx({
        "low": 3000 / 1609.344, "base": 12000 / 1609.344, "high": 36000 / 1609.344,
    })


def test_censored_missing_and_out_of_scope_residents():
    result = sustainability([100, 200, 300, 900], [1000, None, 2000, 99999], [400, 800, 100, 801], censored=[False, False, True, False])
    assert result["scope_population"] == 600
    assert result["usable_population"] == 100
    assert result["baseline_coverage"] == pytest.approx(1 / 6)
    assert result["saved_m"] == 600
    assert sustainability([100], [None], [1])["saved_m"] is None
    assert sustainability([0], [100], [1])["baseline_coverage"] is None


@pytest.mark.parametrize("baseline,proposal", [([1000, 600], [200, 400]), ([100, 0], [800, 800]), ([None, 500], [300, 100])])
def test_added_supply_cannot_increase_nearest_service_distance(baseline, proposal):
    result = sustainability([100, 200], baseline, proposal)
    assert result["after_m"] <= result["before_m"]


def test_midpoint_percentile_ties_constant_and_frozen_reference():
    assert empirical_percentile(20, [10, 20, 20, 30]) == 50
    assert empirical_percentile(5, [10, 20]) == 0
    assert empirical_percentile(25, [10, 20]) == 100
    assert empirical_percentile(999, [20, 20]) == 50
    assert empirical_percentile(10, [10, 20]) == 25


def metrics(**overrides):
    return {"need": 50, "economic": 50, "sustainability": 50, **overrides}


def test_common_dimensions_rescale_and_preserve_raw_alternatives():
    result = rank({"clinic": metrics(economic=None), "grocery": metrics(economic=100)}, {"need": 2, "economic": 10, "sustainability": 1})
    assert result["active_dimensions"] == ["need", "sustainability"]
    assert result["effective_weights"] == pytest.approx({"need": 2 / 3, "economic": 0, "sustainability": 1 / 3})
    assert [r["category_id"] for r in result["alternatives"]] == ["clinic", "grocery"]


def test_eliminated_positively_weighted_dimension_has_null_ranks():
    result = rank({"clinic": metrics(economic=None), "grocery": metrics()}, {"need": 0, "economic": 1, "sustainability": 0})
    assert all(r["rank"] is None and r["total"] is None for r in result["alternatives"])
    assert result["reason"] == "no_weighted_available_objective"


def test_large_finite_weights_rescale_without_overflow():
    result = rank({"clinic": metrics()}, {"need": 1e308, "economic": 0, "sustainability": 1e308})
    assert result["effective_weights"] == {"need": .5, "economic": 0, "sustainability": .5}
    assert result["alternatives"][0]["total"] == 50


def test_priority_can_exceed_100_without_changing_unweighted_pareto():
    result = rank({"clinic": metrics(need=100, economic=100, sustainability=100)}, priorities={"clinic": 2})
    assert result["alternatives"][0]["total"] == pytest.approx(400 / 3)


def test_pareto_tradeoffs_ties_and_dominance_ignore_priorities_and_weights():
    rows = {"clinic": metrics(need=100, economic=0), "grocery": metrics(need=0, economic=100),
            "pharmacy": metrics(need=0, economic=0), "school": metrics(need=100, economic=0)}
    result = rank(rows, {"need": 1, "economic": 0, "sustainability": 0}, {"pharmacy": 2})
    assert {r["category_id"]: r["pareto"] for r in result["alternatives"]} == {
        "clinic": True, "grocery": True, "pharmacy": False, "school": True}
    assert [r["category_id"] for r in result["alternatives"]] == ["clinic", "school", "grocery", "pharmacy"]


@pytest.mark.parametrize("weights,priorities", [
    ({"need": 0, "economic": 0, "sustainability": 0}, {}),
    ({"need": -1, "economic": 1, "sustainability": 1}, {}),
    ({"need": math.nan, "economic": 1, "sustainability": 1}, {}),
    ({"need": True, "economic": 1, "sustainability": 1}, {}),
    ({"need": 1, "economic": 1, "sustainability": 1}, {"clinic": 2.1}),
    ({"need": 1, "economic": 1, "sustainability": 1}, {"unknown": 1}),
])
def test_invalid_weight_inputs(weights, priorities):
    with pytest.raises(ValueError):
        rank({"clinic": metrics()}, weights, priorities)


def test_economic_repeatability_stream_independence_and_exact_zeros():
    assumptions = {c: {"taxable_sales": [100, 200, 300], "local_retention": .01, "jobs": [1, 2, 4]} for c in CANDIDATES}
    first = economic_scenarios(assumptions)
    assert first == economic_scenarios(dict(reversed(list(assumptions.items()))))
    assert first["clinic"] == economic_scenarios({"clinic": assumptions["clinic"]})["clinic"]
    assert first["clinic"] != first["school"]
    zeros = economic_scenarios({"clinic": {"taxable_sales": [0, 0, 0], "local_retention": .01, "jobs": None}})["clinic"]
    assert zeros["retained_tax_usd_per_year"] == {"p10": 0, "p50": 0, "p90": 0}
    assert zeros["operating_fte"] is None
    for summaries in first.values():
        for key, interval in summaries.items():
            lo, hi = (1, 3) if key == "retained_tax_usd_per_year" else (1, 4)
            assert lo <= interval["p10"] <= interval["p50"] <= interval["p90"] <= hi


@pytest.mark.parametrize("pop,distances", [([-1], [[0]]), ([1], [[float('inf')]]), ([1], [[-1]]), ([1, 2], [[0], []])])
def test_invalid_spatial_inputs(pop, distances):
    with pytest.raises(ValueError):
        accessibility(pop, distances)
