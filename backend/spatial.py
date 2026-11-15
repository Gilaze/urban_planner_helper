from dataclasses import dataclass
from statistics import median

import numpy as np
from shapely import distance, points
from shapely.strtree import STRtree

from .geometry import TO_METERS, project
from .schemas import CATEGORIES, Category, Need, Sensitivity, ServiceFeature, Sustainability

INNER_M = 400
CATCHMENT_M = 800
METERS_PER_MILE = 1609.344
TRIPS = Sensitivity(low=0.05, base=0.10, high=0.20)
CAR_SHARE = Sensitivity(low=0.25, base=0.50, high=0.75)


def distance_weights(distances):
    return np.select(
        [distances <= INNER_M, distances <= CATCHMENT_M],
        [1.0, 0.5],
        default=0.0,
    )


def projected_points(coordinates):
    if not len(coordinates):
        return points([], [])
    lon, lat = np.asarray(coordinates).T
    if len(lon) == 1:
        x, y = TO_METERS.transform(float(lon[0]), float(lat[0]))
        return points([x], [y])
    return points(*TO_METERS.transform(lon, lat))


@dataclass
class CategoryBaseline:
    accessibility: np.ndarray
    target: float | None
    nearest_m: np.ndarray
    zero_population_facilities: int


class SpatialScoring:
    def __init__(
        self,
        populations: list[tuple[tuple[float, float], float]],
        services: list[ServiceFeature],
        coverage,
    ):
        self.people = np.asarray([people for _, people in populations], dtype=float)
        self.points = projected_points([coords for coords, _ in populations])
        self.tree = STRtree(self.points)
        border_distance = np.full(len(self.points), -1.0)
        if coverage is not None:
            coverage_m = project(coverage)
            inside = coverage_m.covers(self.points)
            border_distance[inside] = distance(self.points[inside], coverage_m.boundary)
        self.border_distance = border_distance
        self.supported = (self.people > 0) & (border_distance >= 2 * CATCHMENT_M)
        service_points = projected_points([service.geometry.coordinates for service in services])
        self.baselines = {
            category: self._build_baseline(
                service_points[[category in service.properties.categories for service in services]]
            )
            for category in CATEGORIES
        }

    def _build_baseline(self, facilities):
        # Query pairs in bulk and accumulate a two-step floating catchment.
        facilities_index, pop_index = self.tree.query(facilities, predicate="dwithin", distance=CATCHMENT_M)
        weights = distance_weights(distance(facilities[facilities_index], self.points[pop_index]))
        counts = np.bincount(facilities_index, weights * self.people[pop_index], minlength=len(facilities))
        ratios = np.divide(1, counts, out=np.zeros_like(counts, dtype=float), where=counts > 0)
        access = np.bincount(pop_index, ratios[facilities_index] * weights, minlength=len(self.points))
        positive = access[self.supported & (access > 0)]
        nearest_m = np.full(len(self.points), np.inf)
        if len(facilities) and len(self.points):
            pairs, distances = STRtree(facilities).query_nearest(
                self.points, all_matches=False, return_distance=True
            )
            nearest_m[pairs[0]] = distances
        return CategoryBaseline(
            access,
            float(median(positive)) if len(positive) else None,
            nearest_m,
            int((counts == 0).sum()),
        )

    def _need(self, baseline, local, distances, people, covered) -> Need:
        common = dict(
            covered_population=covered,
            target_accessibility=baseline.target,
            zero_population_facilities=baseline.zero_population_facilities,
        )
        if not covered:
            return Need(**common, reason="no_local_population")
        if np.any(~self.supported[local]):
            return Need(**common, reason="incomplete_catchment_coverage")
        if baseline.target is None:
            return Need(**common, reason="no_positive_baseline_accessibility")
        weights = distance_weights(distances)
        before = baseline.accessibility[local]
        after = before + weights / np.dot(people, weights)
        deficit_before = float(np.average(np.maximum(0, 1 - before / baseline.target), weights=people))
        deficit_after = float(np.average(np.maximum(0, 1 - after / baseline.target), weights=people))
        return Need(
            **common,
            deficit_before=deficit_before,
            deficit_after=deficit_after,
            deficit_reduction=deficit_before - deficit_after,
        )

    def _sustainability(self, baseline, local, distances, people, covered) -> Sustainability:
        existing = baseline.nearest_m[local]
        border = self.border_distance[local]
        finite = np.isfinite(existing)
        usable = finite & (existing <= border)
        missing = ~finite & (border >= 0)
        valid_people = people[usable]
        usable_population = float(np.sum(valid_people))
        common = dict(
            local_population=covered,
            usable_baseline_population=usable_population,
            missing_baseline_population=float(np.sum(people[missing])),
            censored_baseline_population=float(np.sum(people[~(usable | missing)])),
            baseline_coverage_pct=100 * usable_population / covered if covered else None,
        )
        if not usable_population:
            reason = "no_usable_baseline" if covered else "no_local_population"
            return Sustainability(**common, reason=reason)

        before = existing[usable]
        after = np.minimum(before, distances[usable])
        miles = float(np.dot(valid_people, before - after)) / METERS_PER_MILE
        scenarios = {
            key: 2 * miles * getattr(TRIPS, key) * getattr(CAR_SHARE, key)
            for key in ("low", "base", "high")
        }
        return Sustainability(
            **common,
            distance_before_m=np.average(before, weights=valid_people),
            distance_after_m=np.average(after, weights=valid_people),
            avoided_vehicle_miles_per_day=Sensitivity(**scenarios),
        )

    def score(self, coordinates) -> dict[Category, tuple[Need, Sustainability]]:
        proposal = projected_points([coordinates])[0]
        local = self.tree.query(proposal, predicate="dwithin", distance=CATCHMENT_M)
        local = np.sort(local[self.people[local] > 0])
        distances = distance(self.points[local], proposal)
        people = self.people[local]
        covered = float(people.sum())
        return {
            category: (
                self._need(baseline, local, distances, people, covered),
                self._sustainability(baseline, local, distances, people, covered),
            )
            for category, baseline in self.baselines.items()
        }
