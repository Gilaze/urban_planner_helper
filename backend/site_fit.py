"""Geometric service concepts. No zoning or permit rules are inferred here."""

from dataclasses import dataclass
from math import atan2, ceil, cos, sin, sqrt

from shapely import affinity
from shapely.geometry import Polygon, shape
from shapely.ops import polylabel

from .geometry import polygon_model, project, unproject
from .planning_schemas import ServiceConcept, SiteContext, SiteDesign


@dataclass
class Rectangle:
    geometry: Polygon
    width: float


def rectangles(available, max_area):
    """Conservative rectangles fully covered by open ground, including concavities/holes."""
    if available.is_empty or max_area <= 0:
        return []
    pieces = [available] if available.geom_type == "Polygon" else list(available.geoms)
    results = []
    for piece in sorted(pieces, key=lambda item: item.area, reverse=True)[:3]:
        if piece.geom_type != "Polygon" or piece.area < 1:
            continue
        anchor = piece.representative_point()
        local = affinity.translate(piece, xoff=-anchor.x, yoff=-anchor.y)
        ring = list(local.minimum_rotated_rectangle.exterior.coords)
        edges = [(a, b) for a, b in zip(ring, ring[1:])]
        a, b = max(edges, key=lambda edge: (edge[1][0] - edge[0][0]) ** 2 + (edge[1][1] - edge[0][1]) ** 2)
        angle = atan2(b[1] - a[1], b[0] - a[0])
        origins = [anchor, polylabel(piece, tolerance=1)]
        if piece.covers(piece.centroid):
            origins.append(piece.centroid)
        for origin in origins:
            for ratio in (1, 1.5, 2, 3):
                def rectangle(area):
                    width = sqrt(area / ratio)
                    length = width * ratio
                    dx, dy = cos(angle), sin(angle)
                    return Polygon([
                        (origin.x + x * dx - y * dy, origin.y + x * dy + y * dx)
                        for x, y in ((-length/2, -width/2), (length/2, -width/2),
                                     (length/2, width/2), (-length/2, width/2))
                    ])
                low, high = 0.0, min(max_area, piece.area)
                if piece.covers(rectangle(high)):
                    low = high
                else:
                    for _ in range(20):
                        mid = (low + high) / 2
                        if piece.covers(rectangle(mid)):
                            low = mid
                        else:
                            high = mid
                if low > 0.01:
                    results.append(Rectangle(rectangle(low), sqrt(low / ratio)))
    return results


def open_ground(context: SiteContext):
    return project(shape(context.available_geometry.model_dump())) if context.available_geometry else Polygon()


def suggest_size(context: SiteContext, program: ServiceConcept, candidates: list[Rectangle], available_land_m2: float):
    notes = ["Illustrative service program; capacity and financial estimates remain for one facility."]
    if not context.buildings_checked:
        notes.append("Existing building footprints have not been checked.")
    elif context.existing_building_count:
        notes.append("Mapped existing building footprints are excluded from the proposed open-ground footprint.")
    base = dict(available_land_m2=available_land_m2, target_floor_area_m2=program.target_floor_area_m2, notes=notes)
    usable = [row for row in candidates if row.width >= program.min_width_m - 1e-6 and row.geometry.area >= program.min_footprint_m2 - 1e-6]
    if not usable:
        reason = "No open ground remains after mapped buildings." if available_land_m2 < 1 else (
            f"No clear footprint fits this concept's {program.min_footprint_m2:g} m² minimum and {program.min_width_m:g} m width."
        )
        return SiteDesign(feasible=False, fit_score=0, reasons=[reason], **base)
    rectangle = max(usable, key=lambda row: row.geometry.area)
    capacity = rectangle.geometry.area
    floors = min(program.max_floors, max(1, ceil(program.target_floor_area_m2 / capacity)))
    target = min(capacity, program.target_floor_area_m2 / floors)
    # Keep both the minimum footprint and clear width after shrinking a large site's concept.
    target = max(target, program.min_footprint_m2, capacity * (program.min_width_m / rectangle.width) ** 2)
    target = min(target, capacity)
    footprint = affinity.scale(rectangle.geometry, xfact=sqrt(target/capacity), yfact=sqrt(target/capacity), origin="centroid")
    floor_area = footprint.area * floors
    program_fit = min(1, floor_area / program.target_floor_area_m2)
    efficiency = min(1, footprint.area / capacity)
    if program_fit < 0.999:
        notes.append("A compact concept fits here; it provides less floor area than the reference program.")
    return SiteDesign(
        feasible=True, fit_score=80 * program_fit + 20 * efficiency,
        geometry=polygon_model(unproject(footprint)), footprint_m2=footprint.area,
        floor_area_m2=floor_area, floors=floors, height_m=floors * program.floor_height_m, **base,
    )
