from typing import Literal

from pydantic import Field, model_validator

from .schemas import CATEGORIES, Category, NonNegative, ParcelGeometry, ParcelID, StrictModel


class ServiceConcept(StrictModel):
    min_footprint_m2: NonNegative = Field(gt=0)
    min_width_m: NonNegative = Field(gt=0)
    target_floor_area_m2: NonNegative = Field(gt=0)
    max_floors: int = Field(ge=1, le=20)
    floor_height_m: NonNegative = Field(gt=0)


class SiteFitAssumptions(StrictModel):
    version: str
    description: str
    max_lot_coverage: NonNegative = Field(gt=0, le=1)
    full_need_gap_pct: NonNegative = Field(gt=0, le=100)
    need_weight: NonNegative
    fit_weight: NonNegative
    benefit_weight: NonNegative
    categories: dict[Category, ServiceConcept]

    @model_validator(mode="after")
    def validate_programs(self):
        if set(self.categories) != set(CATEGORIES):
            raise ValueError("Site-fit assumptions must cover the five service categories")
        if abs(self.need_weight + self.fit_weight + self.benefit_weight - 1) > 1e-9:
            raise ValueError("The three planning score weights must sum to one")
        if any(row.target_floor_area_m2 < row.min_footprint_m2 for row in self.categories.values()):
            raise ValueError("Target floor area must be at least the minimum footprint")
        return self


class AreaNeed(StrictModel):
    category_id: Category
    access_deficit_pct: NonNegative | None = Field(default=None, le=100)
    covered_population: NonNegative
    reason: str | None = None


class AreaProperties(StrictModel):
    name: str
    population: NonNegative
    supported_population: NonNegative
    candidate_count: int = Field(ge=0)
    representative_point: tuple[float, float]
    needs: list[AreaNeed]


class PlanningArea(StrictModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: ParcelGeometry
    properties: AreaProperties


class SiteContext(StrictModel):
    parcel_id: ParcelID
    area_id: str | None
    available_geometry: ParcelGeometry | None
    buildings_checked: bool
    existing_building_count: int = Field(ge=0)
    existing_height_m: NonNegative | None = None


class PlanningSources(StrictModel):
    tract_url: str
    tract_sha256: str
    tract_vintage: str
    population_vintage: str
    building_source: str | None
    demographic_sha256: str


class PlanningIndex(StrictModel):
    schema_version: Literal["planning/1"] = "planning/1"
    data_version: str
    need_targets: dict[Category, NonNegative | None]
    sources: PlanningSources
    areas: list[PlanningArea]
    sites: list[SiteContext]
    unassigned_population_points: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_index(self):
        areas = {area.id for area in self.areas}
        if len(areas) != len(self.areas) or not areas:
            raise ValueError("Planning areas must have unique identifiers")
        if len({site.parcel_id for site in self.sites}) != len(self.sites):
            raise ValueError("Duplicate parcel in planning index")
        if any(site.area_id is not None and site.area_id not in areas for site in self.sites):
            raise ValueError("Unknown area in planning site index")
        if set(self.need_targets) != set(CATEGORIES):
            raise ValueError("Missing planning need targets")
        for area in self.areas:
            if {row.category_id for row in area.properties.needs} != set(CATEGORIES) or len(area.properties.needs) != len(CATEGORIES):
                raise ValueError("Area needs must cover each category exactly once")
        return self


class AreaCollection(StrictModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[PlanningArea]
    data_version: str
    sources: PlanningSources
    unassigned_candidate_count: int = Field(ge=0)
    unassigned_population_points: int = Field(ge=0)


class SiteDesign(StrictModel):
    feasible: bool
    fit_score: NonNegative = Field(le=100)
    geometry: ParcelGeometry | None = None
    footprint_m2: NonNegative = 0
    floor_area_m2: NonNegative = 0
    height_m: NonNegative = 0
    floors: int = Field(default=0, ge=0)
    available_land_m2: NonNegative
    target_floor_area_m2: NonNegative
    reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RankedSite(StrictModel):
    parcel_id: ParcelID
    area_id: str
    rank: int | None
    score: NonNegative | None = Field(le=100)
    need_score: NonNegative | None = Field(le=100)
    fit_score: NonNegative = Field(le=100)
    benefit_score: NonNegative | None = Field(le=100)
    need_level: str
    deficit_reduction_pct: NonNegative | None
    population_weighted_benefit: NonNegative | None
    design: SiteDesign
    reason: str | None = None
    explanation: str


class SiteRanking(StrictModel):
    area_id: str
    category_id: Category
    data_version: str
    assumption_version: str
    need_weight: NonNegative
    fit_weight: NonNegative
    benefit_weight: NonNegative
    tract_access_deficit_pct: NonNegative | None = Field(le=100)
    tract_need_priority: NonNegative = Field(le=1)
    candidate_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    unranked_count: int = Field(ge=0)
    ranked_count: int = Field(ge=0)
    exclusion_reasons: dict[str, int]
    sites: list[RankedSite]


class AreaRecommendations(StrictModel):
    area_id: str
    data_version: str
    rankings: dict[Category, SiteRanking]


class SitePlan(StrictModel):
    parcel_id: ParcelID
    area_id: str | None
    data_version: str
    assumption_version: str
    designs: dict[Category, SiteDesign]
