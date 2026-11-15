import random
from pathlib import Path

import numpy as np
from pydantic import Field, model_validator

from .schemas import CATEGORIES, Category, Economics, Mode, NonNegative, Percentiles, StrictModel


class Triangular(StrictModel):
    low: NonNegative
    mode: NonNegative
    high: NonNegative

    @model_validator(mode="after")
    def ordered(self):
        if not self.low <= self.mode <= self.high:
            raise ValueError("Triangular inputs must be ordered low <= mode <= high")
        return self


class Assumption(StrictModel):
    taxable_sales_usd_per_year: Triangular | None
    local_retention: NonNegative | None = Field(le=1)
    operating_fte: Triangular | None
    source: str
    geography: str
    year: int | None
    notes: str


class Assumptions(StrictModel):
    version: str
    mode: Mode
    seed: int
    categories: dict[Category, Assumption]

    @model_validator(mode="after")
    def all_categories(self):
        if set(self.categories) != set(CATEGORIES):
            raise ValueError("Assumptions must cover all five categories")
        return self


def load_assumptions(path: Path, mode: Mode) -> Assumptions:
    assumptions = Assumptions.model_validate_json(path.read_bytes())
    if assumptions.mode != mode:
        raise ValueError(f"Expected {mode} economic assumptions")
    return assumptions


def _sample(distribution: Triangular, stream: random.Random, multiplier: float = 1) -> Percentiles:
    values = [
        stream.triangular(distribution.low, distribution.high, distribution.mode) * multiplier
        for _ in range(1000)
    ]
    p10, p50, p90 = np.percentile(values, (10, 50, 90))
    return Percentiles(p10=p10, p50=p50, p90=p90)


def summarize_economics(assumptions: Assumptions) -> dict[Category, Economics]:
    summaries = {}
    for category in CATEGORIES:
        row = assumptions.categories[category]
        # Separate streams keep the job draws fixed when sales assumptions change.
        seed = f"{assumptions.seed}:{category}"
        tax = None
        jobs = None
        if row.taxable_sales_usd_per_year is not None and row.local_retention is not None:
            tax = _sample(row.taxable_sales_usd_per_year, random.Random(f"{seed}:tax"), row.local_retention)
        elif row.local_retention == 0 or (
            row.taxable_sales_usd_per_year is not None and row.taxable_sales_usd_per_year.high == 0
        ):
            tax = Percentiles(p10=0, p50=0, p90=0)
        if row.operating_fte is not None:
            jobs = _sample(row.operating_fte, random.Random(f"{seed}:jobs"))
        summaries[category] = Economics(
            retained_sales_tax_usd_per_year=tax,
            operating_fte=jobs,
            source=row.source,
            geography=row.geography,
            year=row.year,
            reason=None if tax is not None and jobs is not None else "economic_inputs_missing",
        )
    return summaries
