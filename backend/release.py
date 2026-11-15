from pathlib import Path

from pydantic import model_validator
from shapely import equals_exact
from shapely.geometry import box, shape
from shapely.strtree import STRtree

from .economics import Assumptions, summarize_economics
from .geometry import massing, parcel_shape, validate_wgs84
from .ranking import BENEFIT_FIELDS, normalize, raw_benefits
from .schemas import (
    CATEGORIES,
    Category,
    Metrics,
    Mode,
    NonNegative,
    ParcelFeature,
    ParcelGeometry,
    ParcelID,
    ReviewActivity,
    ServiceFeature,
    StrictModel,
    Versions,
)
from .spatial import SpatialScoring

ROOT = Path(__file__).resolve().parents[1]


class Population(StrictModel):
    geoid: str
    coordinates: tuple[float, float]
    population: NonNegative


class ReleaseInputs(StrictModel):
    data_version: str
    model_version: str | None = None
    parcel_source_version: str
    parcel_retrieved_at: str | None = None
    sources: dict[str, str]
    coverage: dict[str, str | float | int | None]
    spatial_coverage: ParcelGeometry | None
    parcels: list[ParcelFeature]
    services: list[ServiceFeature]
    populations: list[Population]
    predictions: dict[ParcelID, dict[Category, ReviewActivity]]
    eligible_parcel_ids: set[ParcelID] | None = None

    @model_validator(mode="after")
    def validate_records(self):
        parcel_ids = {parcel.id for parcel in self.parcels}
        if not parcel_ids or len(parcel_ids) != len(self.parcels):
            raise ValueError("A release needs unique, nonempty parcel records")
        if self.eligible_parcel_ids is not None and (
            not self.eligible_parcel_ids or self.eligible_parcel_ids - parcel_ids
        ):
            raise ValueError("Eligible parcel IDs must identify a nonempty subset of the snapshot")
        if set(self.predictions) - parcel_ids:
            raise ValueError("Predictions contain unknown parcel IDs")
        if self.model_version is None and self.predictions:
            raise ValueError("Predictions require a model or baseline version")
        if len({service.id for service in self.services}) != len(self.services):
            raise ValueError("Duplicate place IDs")
        if len({point.geoid for point in self.populations}) != len(self.populations):
            raise ValueError("Duplicate population GEOIDs")
        for parcel in self.parcels:
            validate_parcel(parcel)
        for service in self.services:
            validate_wgs84(shape(service.geometry.model_dump()))
            tags = service.properties.categories
            if service.id != service.properties.place_id or not tags or len(tags) != len(set(tags)):
                raise ValueError(f"Invalid service properties for {service.id}")
        for point in self.populations:
            lon, lat = point.coordinates
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"Invalid population coordinates for {point.geoid}")
        return self


def validate_parcel(parcel: ParcelFeature) -> None:
    geom = validate_wgs84(parcel_shape(parcel))
    point = validate_wgs84(shape(parcel.properties.representative_point.model_dump()))
    if parcel.id != parcel.properties.parcel_id or not geom.covers(point):
        raise ValueError(f"Inconsistent ID or representative point for {parcel.id}")


class FrozenRelease(StrictModel):
    mode: Mode
    versions: Versions
    parcel_source_version: str
    parcel_retrieved_at: str | None
    sources: dict[str, str]
    coverage: dict[str, str | float | int | None]
    parcels: list[ParcelFeature]
    services: list[ServiceFeature]
    need_targets: dict[Category, NonNegative | None]
    references: dict[str, list[NonNegative]]
    comparisons: dict[ParcelID, dict[Category, Metrics]]

    @model_validator(mode="after")
    def validate_indexes(self):
        parcel_ids = {parcel.id for parcel in self.parcels}
        if len(parcel_ids) != len(self.parcels) or not self.comparisons or set(self.comparisons) - parcel_ids:
            raise ValueError("Frozen comparisons must match the parcel snapshot")
        if set(self.need_targets) != set(CATEGORIES):
            raise ValueError("Need targets must cover all categories")
        if set(self.references) != set(BENEFIT_FIELDS) or any(
            values != sorted(values) for values in self.references.values()
        ):
            raise ValueError("Invalid frozen percentile references")
        for parcel in self.parcels:
            validate_parcel(parcel)
        if len({service.id for service in self.services}) != len(self.services):
            raise ValueError("Duplicate place IDs")
        for service in self.services:
            validate_wgs84(shape(service.geometry.model_dump()))
        for rows in self.comparisons.values():
            if set(rows) != set(CATEGORIES):
                raise ValueError("Frozen comparisons must cover all categories")
            for category, metrics in rows.items():
                if (
                    metrics.need.target_accessibility != self.need_targets[category]
                    or metrics.normalized != normalize(metrics, self.references)
                ):
                    raise ValueError("Frozen metrics do not match release references")
                if self.mode == "live" and metrics.review_activity.source == "fixture":
                    raise ValueError("Live releases cannot use synthetic predictions")
        return self


class Release:
    def __init__(self, frozen: FrozenRelease):
        self.frozen = frozen
        self.parcels = {parcel.id: parcel for parcel in frozen.parcels}
        self.parcel_shapes = {parcel.id: parcel_shape(parcel) for parcel in frozen.parcels}
        self.parcel_tree = STRtree(list(self.parcel_shapes.values()))
        self.massing = {parcel_id: massing(self.parcels[parcel_id]) for parcel_id in frozen.comparisons}
        self.service_tree = STRtree([shape(service.geometry.model_dump()) for service in frozen.services])

    def list_parcels(self, bounds: tuple[float, ...]):
        indices = self.parcel_tree.query(box(*bounds), predicate="intersects")
        return sorted((self.frozen.parcels[i] for i in indices), key=lambda parcel: parcel.id)

    def list_services(self, bounds: tuple[float, ...], category: Category | None):
        indices = self.service_tree.query(box(*bounds), predicate="intersects")
        return sorted(
            (
                self.frozen.services[i] for i in indices
                if category is None or category in self.frozen.services[i].properties.categories
            ),
            key=lambda service: service.id,
        )

    def scoring_reason(self, parcel: ParcelFeature) -> str | None:
        snapshot = self.parcel_shapes.get(parcel.id)
        if snapshot is None:
            return "parcel_not_in_scoring_snapshot"
        frozen_properties = self.parcels[parcel.id].properties
        current = parcel_shape(parcel)
        if not snapshot.equals(current) and not equals_exact(snapshot, current, tolerance=1e-10, normalize=True):
            return "parcel_geometry_changed"
        if (
            parcel.properties.vacancy_status != "unknown"
            and frozen_properties.vacancy_status != parcel.properties.vacancy_status
        ):
            return "parcel_vacancy_status_changed"
        if parcel.id not in self.frozen.comparisons:
            return "parcel_not_eligible_for_scoring"
        return None


def freeze(inputs: ReleaseInputs, assumptions: Assumptions) -> FrozenRelease:
    coverage = None
    if inputs.spatial_coverage is not None:
        coverage = validate_wgs84(shape(inputs.spatial_coverage.model_dump()))
    spatial = SpatialScoring(
        [(point.coordinates, point.population) for point in sorted(inputs.populations, key=lambda row: row.geoid)],
        sorted(inputs.services, key=lambda service: service.id),
        coverage,
    )
    economics = summarize_economics(assumptions)
    comparisons = {}
    references = {metric: [] for metric in BENEFIT_FIELDS}
    for parcel in sorted(inputs.parcels, key=lambda feature: feature.id):
        if inputs.eligible_parcel_ids is not None and parcel.id not in inputs.eligible_parcel_ids:
            continue
        spatial_metrics = spatial.score(parcel.properties.representative_point.coordinates)
        predictions = inputs.predictions.get(parcel.id, {})
        comparisons[parcel.id] = {}
        for category, (need, sustainability) in spatial_metrics.items():
            metrics = Metrics(
                need=need,
                economic=economics[category],
                sustainability=sustainability,
                normalized={},
                review_activity=predictions.get(category, ReviewActivity()),
            )
            comparisons[parcel.id][category] = metrics
            for metric, value in raw_benefits(metrics).items():
                if value is not None:
                    references[metric].append(value)
    for values in references.values():
        values.sort()
    for metrics_by_category in comparisons.values():
        for metrics in metrics_by_category.values():
            metrics.normalized = normalize(metrics, references)
    return FrozenRelease(
        mode=assumptions.mode,
        versions=Versions(
            data_version=inputs.data_version,
            model_version=inputs.model_version,
            assumption_version=assumptions.version,
        ),
        parcel_source_version=inputs.parcel_source_version,
        parcel_retrieved_at=inputs.parcel_retrieved_at,
        sources=inputs.sources,
        coverage={
            **inputs.coverage,
            "snapshot_parcels": len(inputs.parcels),
            "scored_parcels": len(comparisons),
            "service_locations": len(inputs.services),
            "population_points": len(inputs.populations),
        },
        parcels=sorted(inputs.parcels, key=lambda feature: feature.id),
        services=sorted(inputs.services, key=lambda service: service.id),
        need_targets={category: baseline.target for category, baseline in spatial.baselines.items()},
        references=references,
        comparisons=comparisons,
    )
