"""Tract needs and vacant-ground rankings over a version-matched planning index."""

from collections import Counter
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point
from shapely.strtree import STRtree

from .geometry import TO_METERS
from .planning_schemas import AreaCollection, AreaRecommendations, PlanningIndex, RankedSite, SiteFitAssumptions, SitePlan, SiteRanking
from .schemas import CATEGORIES
from .site_fit import open_ground, rectangles, suggest_size

SERVICE_NAMES = {
    "bank_credit_union": "bank or credit union", "clinic": "clinic", "grocery": "grocery store",
    "pharmacy": "pharmacy", "school": "school",
}


def need_level(score):
    if score is None:
        return "Need unknown"
    if score >= 75:
        return "High need"
    if score >= 50:
        return "Needed"
    if score >= 25:
        return "May help"
    return "No clear need"


class Planning:
    def __init__(self, index: PlanningIndex, release, assumptions: SiteFitAssumptions):
        if index.data_version != release.frozen.versions.data_version or index.need_targets != release.frozen.need_targets:
            raise ValueError("Planning index does not match the scoring release; rebuild the planning index")
        self.index, self.release, self.assumptions = index, release, assumptions
        self.areas = {area.id: area for area in index.areas}
        self.contexts = {}
        for context in index.sites:
            parcel = release.parcels.get(context.parcel_id)
            if parcel is None or context.parcel_id not in release.frozen.comparisons:
                raise ValueError("Planning index contains a parcel outside the scoring release")
            if parcel.properties.vacancy_status != "source_indicated_vacant":
                raise ValueError("Planning index contains a parcel without source-indicated vacancy")
            self.contexts[context.parcel_id] = context
        self.max_benefit = {
            category: max((self.benefit(parcel_id, category) or 0 for parcel_id in self.contexts), default=0)
            for category in CATEGORIES
        }

    @classmethod
    def load(cls, path: Path, release, assumptions_path: Path):
        return cls(
            PlanningIndex.model_validate_json(path.read_bytes()), release,
            SiteFitAssumptions.model_validate_json(assumptions_path.read_bytes()),
        )

    def collection(self):
        return AreaCollection(
            features=self.index.areas, data_version=self.index.data_version, sources=self.index.sources,
            unassigned_candidate_count=sum(row.area_id is None for row in self.contexts.values()),
            unassigned_population_points=self.index.unassigned_population_points,
        )

    def benefit(self, parcel_id, category):
        need = self.release.frozen.comparisons[parcel_id][category].need
        return None if need.deficit_reduction is None else need.deficit_reduction * need.covered_population

    @lru_cache(maxsize=5)
    def service_locations(self, category):
        coordinates = [service.geometry.coordinates for service in self.release.frozen.services
                       if category in service.properties.categories]
        if not coordinates:
            return STRtree([])
        # Project to meters before measuring; these are map distances, not travel routes.
        x, y = TO_METERS.transform(*zip(*coordinates))
        return STRtree([Point(east, north) for east, north in zip(x, y)])

    def explain_site(self, row, category, priority):
        service = SERVICE_NAMES[category]
        if row.score is None:
            need = "There is not enough information about local need to score this lot."
        elif priority == 0:
            need = f"Local access data shows no clear shortage of this service, so adding another {service} scores low."
        elif priority < .25:
            need = f"Local access data shows only a small shortage of this service, so another {service} is a lower priority."
        elif priority < .75:
            need = f"Local access data shows an unmet need for this service; another {service} could help."
        else:
            need = f"Local access data shows a large shortage of this service, making another {service} a strong priority."
        locations = self.service_locations(category)
        distance = ""
        if len(locations):
            point = Point(*TO_METERS.transform(*self.release.parcels[row.parcel_id].properties.representative_point.coordinates))
            miles = point.distance(locations.geometries[locations.nearest(point)]) / 1609.344
            amount = "less than 0.1 miles" if miles < .05 else f"about {miles:.1f} miles"
            distance = f" The nearest mapped {service} is {amount} away."
        fit = ("This lot has room for the full suggested building size."
               if row.design.floor_area_m2 >= row.design.target_floor_area_m2 * .99
               else "This lot fits a smaller building than the target size, which lowers its match score.")
        return f"{need}{distance} {fit}"

    @lru_cache(maxsize=512)
    def site(self, parcel_id):
        context = self.contexts[parcel_id]
        ground = open_ground(context)
        parcel = self.release.parcels[parcel_id]
        candidates = rectangles(ground, parcel.properties.area_m2 * self.assumptions.max_lot_coverage)
        return SitePlan(
            parcel_id=parcel_id, area_id=context.area_id, data_version=self.index.data_version,
            assumption_version=self.assumptions.version,
            designs={category: suggest_size(context, self.assumptions.categories[category], candidates, ground.area)
                     for category in CATEGORIES},
        )

    def ranking(self, area_id, category, limit=20):
        if area_id not in self.areas:
            raise KeyError(area_id)
        contexts = [row for row in self.contexts.values() if row.area_id == area_id]
        area_needs = {row.category_id: row for row in self.areas[area_id].properties.needs}
        gap = area_needs[category].access_deficit_pct
        priority = min(1, gap / self.assumptions.full_need_gap_pct) if gap is not None else None
        need_weight, fit_weight = self.assumptions.need_weight, self.assumptions.fit_weight
        benefit_weight = self.assumptions.benefit_weight
        exclusions, rows = Counter(), []
        for context in contexts:
            design = self.site(context.parcel_id).designs[category]
            if not design.feasible:
                exclusions.update(design.reasons)
                continue
            benefit = self.benefit(context.parcel_id, category)
            need = self.release.frozen.comparisons[context.parcel_id][category].need
            maximum = self.max_benefit[category]
            benefit_score = None if benefit is None else (100 * benefit / maximum if maximum else 0)
            need_score = None if priority is None else 100 * priority
            raw_score = None if need_score is None or benefit_score is None else (
                need_weight * need_score + fit_weight * design.fit_score + benefit_weight * benefit_score
            )
            score = None if raw_score is None else min(100, int(raw_score + .5))
            rows.append(RankedSite(
                parcel_id=context.parcel_id, area_id=area_id, rank=None, score=score, need_score=need_score,
                fit_score=design.fit_score, population_weighted_benefit=benefit, design=design,
                benefit_score=benefit_score, need_level=need_level(score),
                deficit_reduction_pct=None if need.deficit_reduction is None else 100 * need.deficit_reduction,
                reason=need.reason if benefit is None else None,
                explanation="",
            ))
        rows.sort(key=lambda row: (row.score is None, -(row.score or 0), row.parcel_id))
        for i, row in enumerate(rows, 1):
            if row.score is not None:
                row.rank = i
            row.explanation = self.explain_site(row, category, priority)
        return SiteRanking(
            area_id=area_id, category_id=category, data_version=self.index.data_version,
            assumption_version=self.assumptions.version, need_weight=need_weight,
            fit_weight=fit_weight, benefit_weight=benefit_weight, tract_access_deficit_pct=gap,
            tract_need_priority=priority or 0, candidate_count=len(contexts),
            excluded_count=len(contexts)-len(rows), exclusion_reasons=dict(exclusions),
            unranked_count=sum(row.score is None for row in rows),
            ranked_count=sum(row.score is not None for row in rows), sites=rows[:limit],
        )

    def recommendations(self, area_id):
        return AreaRecommendations(area_id=area_id, data_version=self.index.data_version,
                                   rankings={category: self.ranking(area_id, category, 3) for category in CATEGORIES})
