"""Freeze a backend release from Person 2's canonical artifact directory."""

import argparse
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point as ShapelyPoint

from .assessment import ASSESSOR_SOURCE, enrich_assessments
from .datasf import ASSESSOR_URL, PARCEL_URL, DataSF, ProviderUnavailable, datasf_client
from .economics import load_assumptions
from .geometry import polygon_model, project, validate_wgs84
from .release import ROOT, Population, ReleaseInputs, freeze
from .schemas import (
    CATEGORIES,
    ParcelFeature,
    ParcelGeometry,
    ParcelID,
    ParcelProperties,
    Point,
    ReviewActivity,
    ServiceFeature,
    ServiceProperties,
    StrictModel,
)
from .snapshot import write_atomic


class Manifest(StrictModel):
    data_version: str
    model_version: str | None = None
    parcel_source_version: str
    parcel_retrieved_at: str
    sources: dict[str, str]
    coverage: dict[str, str | float | int | None]
    spatial_coverage: ParcelGeometry | None
    hypothetical_parcel_id: ParcelID | None = None


def read_geometries(path: Path):
    frame = gpd.read_parquet(path)
    if frame.crs is None:
        raise ValueError(f"{path.name} requires a CRS")
    representative_points = None
    if "representative_point" in frame:
        representatives = frame.set_geometry("representative_point")
        if representatives.crs is None:
            raise ValueError(f"{path.name} representative points require a CRS")
        representative_points = representatives.geometry.to_crs(4326)
    frame = frame.to_crs(4326)
    frame["representative_point"] = (
        representative_points if representative_points is not None else frame.geometry.representative_point()
    )
    return frame.to_dict("records"), frame.geometry.name


def optional(value):
    return None if value is None or pd.isna(value) else value


def as_point(geometry) -> Point:
    if not isinstance(geometry, ShapelyPoint):
        raise ValueError("Expected a representative Point geometry")
    validate_wgs84(geometry)
    return Point(coordinates=(geometry.x, geometry.y))


def load_parcels(path: Path) -> list[ParcelFeature]:
    records, geometry_column = read_geometries(path)
    parcels = []
    for row in records:
        polygon = validate_wgs84(row[geometry_column])
        apns = list(row["source_apns"])
        if any(not isinstance(apn, str) for apn in apns) or len(set(apns)) != len(apns):
            raise ValueError("Expected unique string APNs within each physical lot")
        vacancy = row["vacancy_status"]
        properties = ParcelProperties(
            parcel_id=row["parcel_id"],
            source_apns=apns,
            representative_point=as_point(row["representative_point"]),
            area_m2=project(polygon).area,
            existing_use=optional(row.get("existing_use")),
            vacancy_status=vacancy,
            hypothetical=vacancy != "source_indicated_vacant",
            zoning=optional(row.get("zoning")),
            source_vintage=optional(row.get("source_vintage")),
            assessed_land_usd=optional(row.get("assessed_land_usd")),
            assessed_improvement_usd=optional(row.get("assessed_improvement_usd")),
            assessment_year=optional(row.get("assessment_year")),
            quality_flags=["hypothetical_site"] if vacancy != "source_indicated_vacant" else [],
        )
        parcels.append(ParcelFeature(
            id=properties.parcel_id, geometry=polygon_model(polygon), properties=properties
        ))
    return parcels


def load_services(path: Path) -> list[ServiceFeature]:
    results = []
    for row in pd.read_parquet(path).to_dict("records"):
        status = optional(row["closure_status"]) or "unknown"
        if status in {"closed_permanently", "closed_temporarily"}:
            continue
        tags = set(row["service_tags"]) | {row["primary_category"]}
        categories = sorted(set(CATEGORIES) & tags)
        if not categories:
            continue
        place = row["place_id"]
        results.append(ServiceFeature(
            id=place,
            geometry=Point(coordinates=(row["longitude"], row["latitude"])),
            properties=ServiceProperties(place_id=place, categories=categories, status=status),
        ))
    return results


def load_populations(path: Path) -> list[Population]:
    records, _ = read_geometries(path)
    return [
        Population(
            geoid=row["geoid"], coordinates=as_point(row["representative_point"]).coordinates,
            population=row["population"],
        )
        for row in records if optional(row["population"]) is not None
    ]


def load_predictions(path: Path):
    if not path.exists():
        return {}
    predictions = {}
    for row in pd.read_parquet(path).to_dict("records"):
        by_category = predictions.setdefault(row["parcel_id"], {})
        category = row["category_id"]
        if category in by_category:
            raise ValueError("Duplicate review prediction for parcel and category")
        value = optional(row["predicted_log1p_reviews_count"])
        by_category[category] = ReviewActivity(
            predicted_log1p_reviews_count=value,
            source=row["source"],
            reason=None if value is not None else "review_prediction_unavailable",
        )
    return predictions


def build_inputs(directory: Path, assessment_year: int | None = None) -> ReleaseInputs:
    manifest = Manifest.model_validate_json((directory / "backend_manifest.json").read_bytes())
    parcels = load_parcels(directory / "parcels.geoparquet")
    eligible = {parcel.id for parcel in parcels if parcel.properties.vacancy_status == "source_indicated_vacant"}
    if not eligible and manifest.hypothetical_parcel_id:
        eligible = {manifest.hypothetical_parcel_id}
    if not eligible:
        raise ValueError("No source-indicated vacancies. Choose a hypothetical parcel in the manifest.")
    sources = {"parcel_provider": PARCEL_URL, "assessor_roll": ASSESSOR_SOURCE, **manifest.sources}
    coverage = dict(manifest.coverage)
    if assessment_year is not None:
        token = os.getenv("DATASF_APP_TOKEN")
        if not token:
            raise ValueError("DATASF_APP_TOKEN is required for assessor enrichment")
        with datasf_client(token) as client:
            url = os.getenv("PARCEL_PLANNER_ASSESSOR_URL", ASSESSOR_URL)
            parcels, matches = enrich_assessments(parcels, eligible, assessment_year, DataSF(client), url)
        coverage.update(assessment_roll_year=assessment_year, parcels_with_assessor_join=matches)
    return ReleaseInputs(
        **manifest.model_dump(exclude={"hypothetical_parcel_id", "coverage", "sources"}),
        parcels=parcels,
        eligible_parcel_ids=eligible,
        services=load_services(directory / "places.parquet"),
        populations=load_populations(directory / "demographics.geoparquet"),
        predictions=load_predictions(directory / "review_predictions.parquet"),
        sources=sources,
        coverage=coverage,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/release.json")
    parser.add_argument("--assumptions", type=Path, default=ROOT / "config/economics.live.json")
    parser.add_argument("--assessment-year", type=int, help="Optionally join one DataSF closed roll year by verified APN")
    args = parser.parse_args()
    if args.assessment_year is not None and not 1900 <= args.assessment_year <= 2100:
        parser.error("Assessment year must be between 1900 and 2100")
    try:
        assumptions = load_assumptions(args.assumptions, "live")
        release = freeze(build_inputs(args.input, args.assessment_year), assumptions)
        write_atomic(args.output, release.model_dump_json())
    except (KeyError, TypeError, OSError, ProviderUnavailable, ValueError) as exc:
        parser.error(str(exc))
    print(f"Froze {len(release.comparisons)} parcel comparisons for {release.versions.data_version}")


if __name__ == "__main__":
    main()
