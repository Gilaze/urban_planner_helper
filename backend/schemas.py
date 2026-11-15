from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

Category = Literal["bank_credit_union", "clinic", "grocery", "pharmacy", "school"]
CATEGORIES: tuple[Category, ...] = ("bank_credit_union", "clinic", "grocery", "pharmacy", "school")
LABELS: dict[Category, str] = {
    "bank_credit_union": "Bank / credit union",
    "clinic": "Clinic",
    "grocery": "Grocery",
    "pharmacy": "Pharmacy",
    "school": "School",
}
Dimension = Literal["need", "economic", "sustainability"]
DIMENSIONS: tuple[Dimension, ...] = ("need", "economic", "sustainability")
ParcelID = Annotated[str, StringConstraints(pattern=r"^sf:[A-Za-z0-9_-]{1,64}$")]
NonNegative = Annotated[FiniteFloat, Field(ge=0)]
Priority = Annotated[FiniteFloat, Field(ge=0, le=2)]
Position = tuple[FiniteFloat, FiniteFloat]
PolygonCoordinates = tuple[tuple[Position, ...], ...]
Mode = Literal["fixture", "live"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Polygon(StrictModel):
    type: Literal["Polygon"]
    coordinates: PolygonCoordinates


class MultiPolygon(StrictModel):
    type: Literal["MultiPolygon"]
    coordinates: tuple[PolygonCoordinates, ...]


ParcelGeometry = Annotated[Polygon | MultiPolygon, Field(discriminator="type")]


class Point(StrictModel):
    type: Literal["Point"] = "Point"
    coordinates: Position


class ParcelProperties(StrictModel):
    parcel_id: ParcelID
    source_apns: list[str] = Field(default_factory=list)
    representative_point: Point
    area_m2: NonNegative
    existing_use: str | None = None
    vacancy_status: Literal["source_indicated_vacant", "unknown", "not_vacant"] = "unknown"
    hypothetical: bool = True
    zoning: str | None = None
    source_vintage: str | None = None
    assessed_land_usd: NonNegative | None = None
    assessed_improvement_usd: NonNegative | None = None
    assessment_year: int | None = None
    quality_flags: list[str] = Field(default_factory=list)


class ParcelFeature(StrictModel):
    type: Literal["Feature"] = "Feature"
    id: ParcelID
    geometry: ParcelGeometry
    properties: ParcelProperties


class ParcelSource(StrictModel):
    version: str
    freshness: Literal["fixture", "live", "cached", "stale"]
    retrieved_at: str | None = None


class ParcelDetails(ParcelFeature):
    source: ParcelSource
    scoring_available: bool
    scoring_reason: str | None = None


class ParcelCollection(StrictModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[ParcelFeature]
    source: ParcelSource
    limit: int
    truncated: bool


class ServiceProperties(StrictModel):
    place_id: str
    categories: list[Category]
    status: Literal["active", "unknown"]


class ServiceFeature(StrictModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Point
    properties: ServiceProperties


class ServiceCollection(StrictModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[ServiceFeature]
    data_version: str
    limit: int
    truncated: bool


class ObjectiveWeights(StrictModel):
    need: NonNegative = 1
    economic: NonNegative = 1
    sustainability: NonNegative = 1


class CompareRequest(StrictModel):
    parcel_id: ParcelID
    objective_weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    category_priorities: dict[Category, Priority] = Field(default_factory=dict)

    @model_validator(mode="after")
    def positive_weight(self):
        if not any(self.objective_weights.model_dump().values()):
            raise ValueError("At least one objective weight must be positive")
        return self


class Versions(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    data_version: str
    model_version: str | None = None
    assumption_version: str


class Need(StrictModel):
    deficit_before: float | None = None
    deficit_after: float | None = None
    deficit_reduction: float | None = None
    covered_population: NonNegative
    target_accessibility: NonNegative | None = None
    zero_population_facilities: int = 0
    reason: str | None = None


class Sensitivity(StrictModel):
    low: NonNegative
    base: NonNegative
    high: NonNegative


class Sustainability(StrictModel):
    distance_before_m: NonNegative | None = None
    distance_after_m: NonNegative | None = None
    baseline_coverage_pct: NonNegative | None = None
    local_population: NonNegative
    usable_baseline_population: NonNegative
    missing_baseline_population: NonNegative
    censored_baseline_population: NonNegative
    avoided_vehicle_miles_per_day: Sensitivity | None = None
    reason: str | None = None


class Percentiles(StrictModel):
    p10: NonNegative
    p50: NonNegative
    p90: NonNegative


class Economics(StrictModel):
    retained_sales_tax_usd_per_year: Percentiles | None = None
    operating_fte: Percentiles | None = None
    source: str
    geography: str
    year: int | None
    reason: str | None = None


class ReviewActivity(StrictModel):
    predicted_log1p_reviews_count: NonNegative | None = None
    source: Literal["model", "category_baseline", "fixture", "unavailable"] = "unavailable"
    reason: str | None = "review_prediction_unavailable"

    @model_validator(mode="after")
    def check_availability(self):
        available = self.predicted_log1p_reviews_count is not None
        if available != (self.source != "unavailable") or available == (self.reason is not None):
            raise ValueError("Review value, source and reason must agree")
        return self


class NormalizedScores(StrictModel):
    need: float | None = None
    economic: float | None = None
    sustainability: float | None = None
    retained_sales_tax: float | None = None
    operating_fte: float | None = None


class Metrics(StrictModel):
    need: Need
    economic: Economics
    sustainability: Sustainability
    normalized: NormalizedScores
    review_activity: ReviewActivity


class Alternative(Metrics):
    category_id: Category
    category_priority: Priority
    total: float | None = None
    rank: int | None = None
    pareto: bool | None = None


class Massing(StrictModel):
    geometry: ParcelGeometry | None
    height_m: Literal[7] = 7
    illustrative: Literal[True] = True
    reason: str | None = None


class Comparison(Versions):
    mode: Mode
    parcel_id: ParcelID
    parcel_source: ParcelSource
    effective_weights: ObjectiveWeights
    active_dimensions: list[Dimension]
    unavailable_dimensions: list[Dimension]
    alternatives: list[Alternative]
    massing: Massing
    reason: str | None = None


class CategoryInfo(StrictModel):
    id: Category
    label: str


class Health(StrictModel):
    ready: bool
    mode: Mode
    versions: Versions | None


class Metadata(Versions):
    mode: Mode
    categories: list[CategoryInfo]
    objective_weights: ObjectiveWeights
    category_priorities: dict[Category, Priority]
    units: dict[str, str]
    sources: dict[str, str]
    coverage: dict[str, str | float | int | None]
    sustainability_trips_per_person_per_day: Sensitivity
    sustainability_car_share: Sensitivity


class ErrorDetail(StrictModel):
    code: str
    message: str
    issues: list[dict] | None = None


class ErrorResponse(StrictModel):
    error: ErrorDetail
