from bisect import bisect_left, bisect_right

from .schemas import (
    CATEGORIES,
    DIMENSIONS,
    Alternative,
    Category,
    CompareRequest,
    Metrics,
    NormalizedScores,
    ObjectiveWeights,
)

BENEFIT_FIELDS = ("need", "retained_sales_tax", "operating_fte", "sustainability")


def raw_benefits(metrics: Metrics) -> dict[str, float | None]:
    tax = metrics.economic.retained_sales_tax_usd_per_year
    jobs = metrics.economic.operating_fte
    savings = metrics.sustainability.avoided_vehicle_miles_per_day
    return {
        "need": metrics.need.deficit_reduction,
        "retained_sales_tax": tax.p50 if tax else None,
        "operating_fte": jobs.p50 if jobs else None,
        "sustainability": savings.base if savings else None,
    }


def percentile(value: float | None, reference: list[float]) -> float | None:
    if value is None or not reference:
        return None
    if reference[0] == reference[-1]:
        return 50.0
    lower = bisect_left(reference, value)
    upper = bisect_right(reference, value)
    return 100 * (lower + upper) / (2 * len(reference))


def normalize(metrics: Metrics, references: dict[str, list[float]]) -> NormalizedScores:
    values = {
        metric: percentile(value, references[metric])
        for metric, value in raw_benefits(metrics).items()
    }
    tax = values["retained_sales_tax"]
    jobs = values["operating_fte"]
    economic = (tax + jobs) / 2 if tax is not None and jobs is not None else None
    return NormalizedScores(economic=economic, **values)


def rank(metrics: dict[Category, Metrics], request: CompareRequest):
    scores = {category: metrics[category].normalized.model_dump() for category in CATEGORIES}
    dimensions = [axis for axis in DIMENSIONS if all(row[axis] is not None for row in scores.values())]
    weights = request.objective_weights.model_dump()
    largest = max((weights[axis] for axis in dimensions), default=0)
    # Scaling by the largest weight avoids overflow for very large finite inputs.
    scaled = {axis: weights[axis] / largest for axis in dimensions} if largest else {}
    scale_sum = sum(scaled.values())
    effective = ObjectiveWeights(**{
        axis: scaled.get(axis, 0) / scale_sum if scale_sum else 0 for axis in DIMENSIONS
    })
    alternatives = []
    for category in CATEGORIES:
        priority = request.category_priorities.get(category, 1)
        row = scores[category]
        total = None
        if largest:
            total = sum(
                getattr(effective, axis) * row[axis] * (priority if axis == "need" else 1)
                for axis in dimensions
            )
        dominated = any(
            all(other[axis] >= row[axis] for axis in dimensions)
            and any(other[axis] > row[axis] for axis in dimensions)
            for other in scores.values()
        )
        alternatives.append(Alternative(
            **metrics[category].model_dump(),
            category_id=category,
            category_priority=priority,
            total=total,
            pareto=not dominated if dimensions else None,
        ))
    if largest:
        alternatives.sort(key=lambda item: (-item.total, item.category_id))
        for index, item in enumerate(alternatives, start=1):
            item.rank = index
    return alternatives, dimensions, effective
