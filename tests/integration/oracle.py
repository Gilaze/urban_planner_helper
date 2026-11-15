"""Small, deliberately unvectorized reference calculations from the plan.

Distances are supplied in meters, so these checks do not share spatial joins,
feature code, RNG state, or scoring functions with the production backend.
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections.abc import Mapping, Sequence

CANDIDATES = ("clinic", "pharmacy", "grocery", "bank_credit_union", "school")
CATEGORIES = CANDIDATES + (
    "hospital", "dental", "mental_health", "food_assistance", "alternative_finance",
    "childcare", "library", "transit", "social_services", "other",
)
DIMENSIONS = ("need", "economic", "sustainability")
METERS_PER_MILE = 1609.344


def number(value: object, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"Expected a finite number, got {value!r}")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"Invalid numeric value: {value!r}")
    return result


def distance_weight(distance_m: float) -> float:
    distance_m = number(distance_m, minimum=0)
    return 1.0 if distance_m <= 400 else 0.5 if distance_m <= 800 else 0.0


def accessibility(populations: Sequence[float], distances_m: Sequence[Sequence[float]]) -> dict:
    """Rows are population points, columns are unit-capacity facilities."""
    pop = [number(p, minimum=0) for p in populations]
    if len(distances_m) != len(pop):
        raise ValueError("One distance row is required for each population point")
    facilities = len(distances_m[0]) if pop else 0
    if any(len(row) != facilities for row in distances_m):
        raise ValueError("Distance matrix must be rectangular")
    weights = [[distance_weight(d) for d in row] for row in distances_m]
    ratios, skipped = [], []
    for j in range(facilities):
        denominator = sum(p * weights[i][j] for i, p in enumerate(pop))
        ratios.append(1 / denominator if denominator else 0.0)
        if denominator == 0:
            skipped.append(j)
    return {
        "accessibility": [sum(r * w for r, w in zip(ratios, row)) for row in weights],
        "facility_ratios": ratios,
        "zero_population_facilities": skipped,
    }


def need(populations, existing_distances_m, proposed_distances_m, *, target=None) -> dict:
    if len(proposed_distances_m) != len(populations):
        raise ValueError("Proposal distances must align with population points")
    before = accessibility(populations, existing_distances_m)
    after = accessibility(populations, [
        [*row, d] for row, d in zip(existing_distances_m, proposed_distances_m)
    ])
    scope = [i for i, d in enumerate(proposed_distances_m) if distance_weight(d) > 0]
    covered_population = sum(populations[i] for i in scope)
    # Freeze this target on the release-wide baseline, never on the new supply.
    positive = [a for p, a in zip(populations, before["accessibility"]) if p > 0 and a > 0]
    target = statistics.median(positive) if target is None and positive else target
    result = {
        "before": before, "after": after, "target": target,
        "covered_population": covered_population,
        "deficit_before": None, "deficit_after": None, "benefit": None,
        "reason": None,
    }
    if target is None:
        result["reason"] = "no_positive_baseline"
    elif covered_population == 0:
        result["reason"] = "zero_population"
    else:
        target = number(target, minimum=0)
        if target == 0:
            raise ValueError("Target must be positive")
        for key, access in (("deficit_before", before), ("deficit_after", after)):
            result[key] = sum(
                populations[i] * max(0, 1 - access["accessibility"][i] / target)
                for i in scope
            ) / covered_population
        result["benefit"] = result["deficit_before"] - result["deficit_after"]
    return result


def sustainability(populations, baseline_distances_m, proposed_distances_m, *, censored=None):
    """Use the same <=800 m proposal scope, including uncovered residents in coverage."""
    if len({len(populations), len(baseline_distances_m), len(proposed_distances_m)}) != 1:
        raise ValueError("Population and distance arrays must align")
    pop = [number(p, minimum=0) for p in populations]
    proposal = [number(d, minimum=0) for d in proposed_distances_m]
    censored = [False] * len(pop) if censored is None else censored
    if len(censored) != len(pop) or any(type(c) is not bool for c in censored):
        raise ValueError("Censor flags must be aligned booleans")
    baseline = [None if d is None else number(d, minimum=0) for d in baseline_distances_m]
    scope = [i for i, d in enumerate(proposal) if d <= 800]
    usable = [i for i in scope if baseline[i] is not None and not censored[i]]
    total_pop = sum(pop[i] for i in scope)
    usable_pop = sum(pop[i] for i in usable)
    result = {
        "scope_population": total_pop, "usable_population": usable_pop,
        "baseline_coverage": usable_pop / total_pop if total_pop else None,
        "before_m": None, "after_m": None, "saved_m": None,
        "avoided_vehicle_miles_per_day": None,
        "reason": "no_usable_baseline" if usable_pop == 0 else None,
    }
    if usable_pop:
        before = sum(pop[i] * baseline[i] for i in usable) / usable_pop
        after = sum(pop[i] * min(baseline[i], proposal[i]) for i in usable) / usable_pop
        person_miles = sum(
            pop[i] * max(0, baseline[i] - proposal[i]) / METERS_PER_MILE for i in usable
        )
        result.update(before_m=before, after_m=after, saved_m=before - after)
        result["avoided_vehicle_miles_per_day"] = {
            label: person_miles * trips * car_share * 2
            for label, trips, car_share in (
                ("low", 0.05, 0.25), ("base", 0.10, 0.50), ("high", 0.20, 0.75)
            )
        }
    return result


def empirical_percentile(value: float, reference: Sequence[float]) -> float:
    """Midpoint empirical CDF: 100 * (below + equal / 2) / n; constant => 50.

    The plan leaves the interpolation convention unspecified. This convention
    must be agreed with Backend before comparing production percentile values.
    """
    value = number(value)
    ref = [number(x) for x in reference]
    if not ref:
        raise ValueError("A frozen reference must not be empty")
    if min(ref) == max(ref):
        return 50.0
    return 100 * (sum(x < value for x in ref) + sum(x == value for x in ref) / 2) / len(ref)


def rank(alternatives: Mapping[str, Mapping[str, float | None]], weights=None, priorities=None):
    weights = dict.fromkeys(DIMENSIONS, 1.0) if weights is None else dict(weights)
    if set(weights) != set(DIMENSIONS):
        raise ValueError("Exactly three objective weights are required")
    weights = {d: number(w, minimum=0) for d, w in weights.items()}
    if not any(weights.values()):
        raise ValueError("Explicit all-zero weights are invalid")
    if not alternatives or set(alternatives) - set(CANDIDATES):
        raise ValueError("Alternatives must use candidate category IDs")
    priorities = {} if priorities is None else dict(priorities)
    if set(priorities) - set(CANDIDATES):
        raise ValueError("Unknown category priority")
    for p in priorities.values():
        if number(p, minimum=0) > 2:
            raise ValueError("Priorities must be in [0, 2]")
    for metrics in alternatives.values():
        if set(metrics) != set(DIMENSIONS):
            raise ValueError("Every alternative must explicitly include all dimensions")
        for v in metrics.values():
            if v is not None and not 0 <= number(v) <= 100:
                raise ValueError("Normalized metrics must be in [0, 100]")
    active = [d for d in DIMENSIONS if all(a[d] is not None for a in alternatives.values())]
    denominator = sum(weights[d] for d in active)
    effective = {d: weights[d] / denominator if d in active and denominator else 0.0 for d in DIMENSIONS}
    rows = []
    for category, metrics in alternatives.items():
        dominated = any(
            all(other[d] >= metrics[d] for d in active)
            and any(other[d] > metrics[d] for d in active)
            for other in alternatives.values()
        ) if active else False
        rows.append({
            "category_id": category,
            "total": sum(effective[d] * metrics[d] * (
                priorities.get(category, 1) if d == "need" else 1
            ) for d in active) if denominator else None,
            "rank": None, "pareto": not dominated if active else None,
        })
    rows.sort(key=lambda r: (-(r["total"] or 0), r["category_id"]))
    if denominator:
        for position, row in enumerate(rows, start=1):
            row["rank"] = position
    return {"active_dimensions": active, "effective_weights": effective, "alternatives": rows,
            "reason": None if denominator else "no_weighted_available_objective"}


def quantile(values, q):
    ordered = sorted(number(v) for v in values)
    if not ordered or not 0 <= q <= 1:
        raise ValueError("Quantile needs data and q in [0, 1]")
    index = (len(ordered) - 1) * q
    lo = math.floor(index)
    return ordered[lo] + (ordered[math.ceil(index)] - ordered[lo]) * (index - lo)


def economic_scenarios(assumptions: Mapping[str, dict], *, seed=42, draws=1000):
    """Independent streams; exact RNG parity with production is not assumed."""
    if draws < 1:
        raise ValueError("At least one draw is required")
    if set(assumptions) - set(CANDIDATES):
        raise ValueError("Unknown economic category")
    result = {}
    for category in sorted(assumptions):
        inputs = assumptions[category]
        summaries = {}
        for dimension, scale in (("taxable_sales", inputs.get("local_retention")), ("jobs", 1)):
            triple = inputs.get(dimension)
            if triple is None or scale is None:
                summaries["retained_tax_usd_per_year" if dimension == "taxable_sales" else "operating_fte"] = None
                continue
            if len(triple) != 3:
                raise ValueError("Triangular inputs must have low, mode, high")
            low, mode, high = [number(v, minimum=0) for v in triple]
            if not low <= mode <= high:
                raise ValueError("Triangular inputs must be ordered")
            scale = number(scale, minimum=0)
            if dimension == "taxable_sales" and scale > 1:
                raise ValueError("Retention must be a fraction")
            stream_seed = int.from_bytes(hashlib.sha256(f"{seed}:{category}:{dimension}".encode()).digest(), "big")
            rng = random.Random(stream_seed)
            samples = [rng.triangular(low, high, mode) * scale for _ in range(draws)]
            summaries["retained_tax_usd_per_year" if dimension == "taxable_sales" else "operating_fte"] = {
                f"p{int(q * 100)}": quantile(samples, q) for q in (0.1, 0.5, 0.9)
            }
        result[category] = summaries
    return result
