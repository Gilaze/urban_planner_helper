import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .datasf import DataSF, DataSFParcels, ProviderUnavailable, datasf_client
from .economics import load_assumptions
from .geometry import massing
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
        west, south, east, north = (float(value) for value in bbox.split(","))
    except ValueError:
        raise APIError(422, "invalid_bbox", "Expected four comma-separated WGS84 coordinates") from None
    if not (
        -180 <= west < east <= 180 and -90 <= south < north <= 90
        and east - west <= 0.5 and north - south <= 0.5
    ):
        raise APIError(422, "invalid_bbox", "Expected ordered bounds spanning at most 0.5° per axis")
    return west, south, east, north


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
    if release is None or release.scoring_reason(parcel) is not None:
        return parcel
    # Carry over dated assessor fields and the representative point used for scoring.
    frozen = release.parcels[parcel.id].properties
    current = parcel.properties
    properties = current.model_copy(update={
        "source_apns": frozen.source_apns,
        "representative_point": frozen.representative_point,
        "zoning": frozen.zoning,
        "assessed_land_usd": frozen.assessed_land_usd,
        "assessed_improvement_usd": frozen.assessed_improvement_usd,
        "assessment_year": frozen.assessment_year,
        "quality_flags": [
            flag for flag in current.quality_flags
            if flag != "apns_unverified" or not frozen.source_apns
        ],
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
) -> FastAPI:
    mode = mode or os.getenv("PARCEL_PLANNER_MODE", "fixture")
    if mode not in {"fixture", "live"}:
        raise ValueError("PARCEL_PLANNER_MODE must be fixture or live")
    release_path = release_path or Path(os.getenv("PARCEL_PLANNER_RELEASE", str(ROOT / "artifacts/release.json")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            app.state.release = load_release(mode, release_path)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Unable to load the %s scoring release", mode)
            app.state.release = None
        token = os.getenv("DATASF_APP_TOKEN")
        if mode == "live" and parcel_provider is None and token:
            with datasf_client(token) as client:
                app.state.parcel_provider = DataSFParcels(DataSF(client))
                yield
        else:
            app.state.parcel_provider = parcel_provider if mode == "live" else None
            yield

    app = FastAPI(title="Parcel Planner", version="1.0", lifespan=lifespan)
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

    frontend = frontend_dir or ROOT / "frontend/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
