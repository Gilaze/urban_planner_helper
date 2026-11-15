import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import bundled_data
from .datasf import DataSF, DataSFParcels, ProviderUnavailable, datasf_client
from .economics import load_assumptions
from .geometry import massing, validate_bbox
from .permits import PermitChecklist, PermitRequest, permit_checklist
from .planning import Planning
from .planning_schemas import AreaCollection, AreaRecommendations, AvailableSites, SitePlan, SiteRanking
from .ranking import rank
from .release import ROOT, FrozenRelease, Release, ReleaseInputs, freeze
from .schemas import (
    CATEGORIES,
    DIMENSIONS,
    LABELS,
    Category,
    CategoryInfo,
    CompareRequest,
    Comparison,
    Economics,
    ErrorDetail,
    ErrorResponse,
    Health,
    Metadata,
    Metrics,
    Mode,
    Need,
    ObjectiveWeights,
    ParcelCollection,
    ParcelDetails,
    ParcelID,
    ParcelSource,
    ServiceCollection,
    Sustainability,
)
from .spatial import CAR_SHARE, TRIPS

VIEWPORT_LIMIT = 500
UNITS = {
    "area": "m²",
    "height": "m",
    "distance": "m",
    "deficit": "fraction",
    "accessibility": "locations per person",
    "population": "people",
    "baseline_coverage": "%",
    "retained_sales_tax": "USD/year",
    "operating_jobs": "FTE",
    "avoided_vehicle_miles": "vehicle miles/day",
    "review_activity": "log1p(review count)",
    "normalized_score": "percentile (0–100)",
    "total": "ranking points",
}


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.detail = ErrorDetail(code=code, message=message)


def error_response(status: int, detail: ErrorDetail):
    return JSONResponse(status_code=status, content=ErrorResponse(error=detail).model_dump())


def parse_bbox(bbox: str = Query(description="WGS84 west,south,east,north. Maximum span 0.5° per axis.")):
    try:
        return validate_bbox(bbox)
    except ValueError as exc:
        raise APIError(422, "invalid_bbox", str(exc)) from None


def require_release(request: Request) -> Release:
    if request.app.state.release is None:
        raise APIError(503, "artifacts_unavailable", "A validated scoring release is unavailable")
    return request.app.state.release


def snapshot_source(release: Release):
    frozen = release.frozen
    return ParcelSource(
        version=frozen.parcel_source_version,
        freshness="fixture" if frozen.mode == "fixture" else "stale",
        retrieved_at=frozen.parcel_retrieved_at,
    )


def resolve_parcel(parcel_id: str, release: Release | None, provider: DataSFParcels | None):
    if provider is not None:
        try:
            parcel, source = provider.detail(parcel_id)
        except ProviderUnavailable:
            pass
        else:
            if parcel is None:
                raise APIError(404, "parcel_not_found", "No parcel matched the supplied ID")
            return enrich_parcel(parcel, release), source
    parcel = release.parcels.get(parcel_id) if release is not None else None
    if parcel is None:
        if release is not None and release.frozen.mode == "fixture":
            raise APIError(404, "parcel_not_found", "No parcel matched the supplied ID")
        raise APIError(503, "parcel_provider_unavailable", "The provider and verified parcel cache are unavailable")
    return parcel, snapshot_source(release)


def enrich_parcel(parcel, release: Release | None):
    if release is None or release.scoring_reason(parcel) not in {None, "parcel_not_eligible_for_scoring"}:
        return parcel
    # Carry over dated assessor fields and the representative point used for scoring.
    frozen = release.parcels[parcel.id].properties
    current = parcel.properties
    flags = list(current.quality_flags)
    if frozen.source_apns and "apns_unverified" in flags:
        flags.remove("apns_unverified")
    if frozen.vacancy_status != "unknown" and "vacancy_unverified" in flags:
        flags.remove("vacancy_unverified")
    flags.extend(flag for flag in frozen.quality_flags if flag.startswith("assessor_"))
    properties = current.model_copy(update={
        "source_apns": frozen.source_apns,
        "representative_point": frozen.representative_point,
        "vacancy_status": frozen.vacancy_status,
        "zoning": frozen.zoning,
        "assessed_land_usd": frozen.assessed_land_usd,
        "assessed_improvement_usd": frozen.assessed_improvement_usd,
        "assessment_year": frozen.assessment_year,
        "quality_flags": list(dict.fromkeys(flags)),
    })
    return parcel.model_copy(update={"properties": properties})


def load_release(mode: Mode, release_path: Path) -> Release:
    if mode == "fixture":
        inputs = ReleaseInputs.model_validate_json((ROOT / "contracts/fixture-inputs.json").read_bytes())
        assumptions = load_assumptions(ROOT / "config/economics.fixture.json", mode)
        return Release(freeze(inputs, assumptions))
    frozen = FrozenRelease.model_validate_json(release_path.read_bytes())
    if frozen.mode != "live":
        raise ValueError("Live mode requires a live release")
    return Release(frozen)


def create_app(
    mode: Mode | None = None,
    release_path: Path | None = None,
    frontend_dir: Path | None = None,
    parcel_provider: DataSFParcels | None = None,
    planning_path: Path | None = None,
) -> FastAPI:
    release_override = release_path is not None or bool(os.getenv("PARCEL_PLANNER_RELEASE"))
    planning_override = planning_path is not None or bool(os.getenv("PARCEL_PLANNER_PLANNING"))
    mode = mode or os.getenv("PARCEL_PLANNER_MODE") or ("live" if bundled_data.available() else "fixture")
    if mode not in {"fixture", "live"}:
        raise ValueError("PARCEL_PLANNER_MODE must be fixture or live")
    release_path = release_path or Path(os.getenv("PARCEL_PLANNER_RELEASE", str(ROOT / "artifacts/release.json")))
    planning_file = planning_path or Path(os.getenv("PARCEL_PLANNER_PLANNING", str(ROOT / "artifacts/planning/index.json")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_release, resolved_planning = release_path, planning_file
        try:
            if mode == "live" and bundled_data.available() and not release_override:
                resolved_release, bundled_index = bundled_data.paths()
                if not planning_override:
                    resolved_planning = bundled_index
            app.state.release = load_release(mode, resolved_release)
            if mode == "live" and bundled_data.available() and release_override and not planning_override:
                # Explicit releases still undergo the planning data-version/target checks below.
                _, resolved_planning = bundled_data.paths()
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Unable to load the %s scoring release", mode)
            app.state.release = None
        app.state.planning = None
        app.state.planning_message = "Tract planning data is not loaded. Parcel comparisons remain available."
        if app.state.release is not None and (mode == "live" or planning_path is not None) and resolved_planning.is_file():
            try:
                app.state.planning = Planning.load(resolved_planning, app.state.release, ROOT / "config/site-fit.json")
            except (OSError, ValueError) as exc:
                logging.getLogger(__name__).warning("Tract planning unavailable: %s", exc)
                app.state.planning_message = "Tract planning data does not match this release and needs refreshing."
        token = os.getenv("DATASF_APP_TOKEN")
        if mode == "live" and parcel_provider is None and token:
            with datasf_client(token) as client:
                app.state.parcel_provider = DataSFParcels(DataSF(client))
                yield
        else:
            app.state.parcel_provider = parcel_provider if mode == "live" else None
            yield

    app = FastAPI(title="Tract Planner", version="1.0", lifespan=lifespan)
    origins = os.getenv("PARCEL_PLANNER_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, exc: APIError):
        return error_response(exc.status, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError):
        issues = [
            {"field": list(issue["loc"]), "type": issue["type"], "message": issue["msg"]}
            for issue in exc.errors()
        ]
        return error_response(422, ErrorDetail(
            code="invalid_request", message="Request validation failed", issues=issues
        ))

    errors = {422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}
    router = APIRouter(prefix="/api/v1")

    def require_planning(request: Request):
        planning = request.app.state.planning
        if planning is None:
            raise APIError(503, "planning_unavailable", request.app.state.planning_message)
        return planning

    @router.get("/planning/areas", response_model=AreaCollection, responses=errors)
    def planning_areas(planning: Planning = Depends(require_planning)):
        return planning.collection()

    @router.get("/planning/areas/{area_id}/sites", response_model=SiteRanking, responses=errors)
    def planning_sites(area_id: str, category: Category, limit: int = 20,
                       parcel_id: ParcelID | None = None, planning: Planning = Depends(require_planning)):
        if not 1 <= limit <= 100:
            raise APIError(422, "invalid_request", "Site limit must be between 1 and 100")
        try:
            if parcel_id is not None:
                context = planning.contexts.get(parcel_id)
                if context is None or context.area_id != area_id:
                    raise APIError(422, "site_outside_tract", "Choose a vacant lot inside the selected tract.")
                design = planning.site(parcel_id).designs[category]
                if not design.feasible:
                    raise APIError(422, "site_does_not_fit", "This lot cannot fit this building concept. " + " ".join(design.reasons))
                result = planning.ranking(area_id, category, len(planning.contexts))
                result.sites = [site for site in result.sites if site.parcel_id == parcel_id]
                return result
            return planning.ranking(area_id, category, limit)
        except KeyError:
            raise APIError(404, "area_not_found", "No tract matched this identifier") from None

    @router.get("/planning/areas/{area_id}/recommendations", response_model=AreaRecommendations, responses=errors)
    def area_recommendations(area_id: str, planning: Planning = Depends(require_planning)):
        try:
            return planning.recommendations(area_id)
        except KeyError:
            raise APIError(404, "area_not_found", "No tract matched this identifier") from None

    @router.get("/planning/areas/{area_id}/available-sites", response_model=AvailableSites, responses=errors)
    def available_sites(area_id: str, category: Category, planning: Planning = Depends(require_planning)):
        try:
            result = planning.ranking(area_id, category, len(planning.contexts))
            return AvailableSites(area_id=area_id, category_id=category, data_version=planning.index.data_version,
                                  features=[planning.release.parcels[site.parcel_id] for site in result.sites])
        except KeyError:
            raise APIError(404, "area_not_found", "No tract matched this identifier") from None

    @router.get("/planning/sites/{parcel_id}", response_model=SitePlan, responses=errors)
    def planning_site(parcel_id: ParcelID, planning: Planning = Depends(require_planning)):
        try:
            return planning.site(parcel_id)
        except KeyError:
            raise APIError(404, "planning_site_not_found", "This parcel is not a source-indicated vacant planning site") from None

    @router.get("/health", response_model=Health, responses={503: {"model": Health}})
    def health(request: Request):
        release = request.app.state.release
        if release is None:
            return JSONResponse(status_code=503, content=Health(ready=False, mode=mode, versions=None).model_dump())
        return Health(ready=True, mode=mode, versions=release.frozen.versions)

    @router.get("/metadata", response_model=Metadata, responses=errors)
    def metadata(release: Release = Depends(require_release)):
        frozen = release.frozen
        return Metadata(
            **frozen.versions.model_dump(),
            mode=mode,
            categories=[CategoryInfo(id=category, label=LABELS[category]) for category in CATEGORIES],
            objective_weights=ObjectiveWeights(),
            category_priorities=dict.fromkeys(CATEGORIES, 1),
            units=UNITS,
            sources=frozen.sources,
            coverage=frozen.coverage,
            sustainability_trips_per_person_per_day=TRIPS,
            sustainability_car_share=CAR_SHARE,
        )

    @router.get("/parcels", response_model=ParcelCollection, responses=errors)
    def parcels(request: Request, bounds: tuple = Depends(parse_bbox)):
        provider = request.app.state.parcel_provider
        release = request.app.state.release
        if provider is not None:
            try:
                features, truncated, source = provider.viewport(bounds, VIEWPORT_LIMIT)
            except ProviderUnavailable:
                pass
            else:
                return ParcelCollection(
                    features=[enrich_parcel(parcel, release) for parcel in features],
                    source=source,
                    limit=VIEWPORT_LIMIT,
                    truncated=truncated,
                )
        if release is None:
            raise APIError(503, "parcel_provider_unavailable", "The provider and verified parcel cache are unavailable")
        features = release.list_parcels(bounds)
        return ParcelCollection(
            features=features[:VIEWPORT_LIMIT],
            source=snapshot_source(release),
            limit=VIEWPORT_LIMIT,
            truncated=len(features) > VIEWPORT_LIMIT,
        )

    parcel_errors = {**errors, 404: {"model": ErrorResponse}}

    @router.get("/parcels/{parcel_id}", response_model=ParcelDetails, responses=parcel_errors)
    def parcel_details(parcel_id: ParcelID, request: Request):
        release = request.app.state.release
        parcel, source = resolve_parcel(parcel_id, release, request.app.state.parcel_provider)
        reason = release.scoring_reason(parcel) if release is not None else "artifacts_unavailable"
        return ParcelDetails(
            **parcel.model_dump(), source=source, scoring_available=reason is None, scoring_reason=reason
        )

    @router.get("/services", response_model=ServiceCollection, responses=errors)
    def services(
        category: Category | None = None,
        bounds: tuple = Depends(parse_bbox),
        release: Release = Depends(require_release),
    ):
        features = release.list_services(bounds, category)
        return ServiceCollection(
            features=features[:VIEWPORT_LIMIT],
            data_version=release.frozen.versions.data_version,
            limit=VIEWPORT_LIMIT,
            truncated=len(features) > VIEWPORT_LIMIT,
        )

    @router.post("/permits/check", response_model=PermitChecklist, responses=parcel_errors)
    def check_permits(payload: PermitRequest, request: Request):
        parcel, _ = resolve_parcel(payload.parcel_id, request.app.state.release, request.app.state.parcel_provider)
        return permit_checklist(parcel, payload, fixture=mode == "fixture")

    @router.post("/scenarios/compare", response_model=Comparison, responses=parcel_errors)
    def compare(payload: CompareRequest, request: Request, release: Release = Depends(require_release)):
        parcel, source = resolve_parcel(payload.parcel_id, release, request.app.state.parcel_provider)
        reason = release.scoring_reason(parcel)
        if reason is None:
            metrics = release.frozen.comparisons[parcel.id]
            footprint = release.massing[parcel.id]
        else:
            unavailable = Metrics(
                need=Need(covered_population=0, reason=reason),
                economic=Economics(source="", geography="", year=None, reason=reason),
                sustainability=Sustainability(
                    local_population=0,
                    usable_baseline_population=0,
                    missing_baseline_population=0,
                    censored_baseline_population=0,
                    reason=reason,
                ),
                normalized={},
                review_activity={"reason": reason},
            )
            metrics = dict.fromkeys(CATEGORIES, unavailable)
            footprint = massing(parcel)
        alternatives, active, effective = rank(metrics, payload)
        if reason is None and not any(effective.model_dump().values()):
            reason = "no_available_positively_weighted_objectives"
        return Comparison(
            **release.frozen.versions.model_dump(),
            mode=mode,
            parcel_id=parcel.id,
            parcel_source=source,
            effective_weights=effective,
            active_dimensions=active,
            unavailable_dimensions=[axis for axis in DIMENSIONS if axis not in active],
            alternatives=alternatives,
            massing=footprint,
            reason=reason,
        )

    app.include_router(router)

    @app.api_route("/api/{remaining:path}", methods=["GET", "POST"], include_in_schema=False)
    def unknown_api_route(remaining: str):
        raise APIError(404, "route_not_found", "No API route matched the request")

    frontend = frontend_dir if frontend_dir is not None else ROOT / "simulator/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
