import httpx
from pydantic import Field
from shapely import box
from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.ops import unary_union

from .geometry import parcel_shape
from .schemas import Category, NonNegative, ParcelFeature, ParcelID, StrictModel

PLANNING = "https://sfplanning.org/permits"
ZONING = "https://sfplanning.org/zoning"
HEALTH = "https://www.sf.gov/check-if-your-project-requires-health-plan-review"
USE_SIZE = "https://codelibrary.amlegal.com/codes/san_francisco/latest/sf_planning/0-0-0-58264"
CUA = "https://sfplanning.org/resource/cua-supplemental"


class PermitRequest(StrictModel):
    parcel_id: ParcelID
    category_id: Category
    floor_area_sqft: NonNegative | None = Field(default=None, gt=0)


class PermitStep(StrictModel):
    title: str
    detail: str
    url: str


class PermitChecklist(StrictModel):
    parcel_id: ParcelID
    category_id: Category
    zoning_districts: list[str]
    special_use_districts: list[str]
    zoning_note: str
    steps: list[PermitStep]


def mapped_districts(client, dataset, field, geometry):
    bounds = box(*geometry.bounds)
    response = client.get(
        f"https://data.sf.gov/resource/{dataset}.json",
        params={"$select": f"the_geom,{field}", "$where": f"intersects(the_geom, '{bounds.wkt}')"},
    )
    response.raise_for_status()
    districts = {}
    for row in response.json():
        boundary = shape(row["the_geom"])
        if boundary.intersection(geometry).area > 0:
            districts.setdefault(row[field], []).append(boundary)
    return districts


def permit_checklist(parcel: ParcelFeature, proposal: PermitRequest, fixture: bool) -> PermitChecklist:
    geometry = parcel_shape(parcel)
    recorded = parcel.properties.zoning
    districts = [recorded] if recorded and not fixture else []
    overlays = []
    single_district = False
    note = "Demo parcel. Confirm zoning for a real site in the Property Information Map."
    if not fixture:
        note = "Current map lookup unavailable. Confirm the parcel's zoning and overlays with SF Planning."
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
                zones = mapped_districts(client, "3i4a-hu95", "zoning", geometry)
                special_uses = mapped_districts(client, "rxfc-8aap", "name", geometry)
            districts = sorted(zones)
            overlays = sorted(special_uses)
            single_district = len(zones) == 1 and unary_union(next(iter(zones.values()))).covers(geometry)
            note = (
                "Confirm applicable rules and project details with SF Planning."
                if single_district else
                "The map did not resolve the full parcel to one district. Ask SF Planning to review the site."
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, GEOSException):
            if districts:
                note += " The district shown is from the parcel snapshot."

    steps = [
        PermitStep(title="Confirm the proposed use", detail=note, url=ZONING),
        PermitStep(
            title="Planning approval and building permit",
            detail="Ask the Permit Center which applications, plans, and reviews apply to the new construction.",
            url=PLANNING,
        ),
    ]
    if overlays:
        steps.insert(1, PermitStep(
            title="Review special use district rules",
            detail="Ask SF Planning how the mapped special use districts affect this proposal.",
            url="https://data.sf.gov/d/rxfc-8aap",
        ))
    if proposal.category_id == "grocery":
        if single_district and districts == ["NC-2"]:
            area = proposal.floor_area_sqft
            if area is None:
                detail = "Enter the grocery's gross floor area to screen the NC-2 use-size threshold."
            elif area > 4000:
                detail = "The area exceeds the NC-2 4,000 sq ft use-size threshold. Ask Planning whether Conditional Use Authorization or an exception applies."
                steps.append(PermitStep(
                    title="Conditional Use Authorization application",
                    detail="Ask SF Planning to confirm which supplemental applications are needed.",
                    url=CUA,
                ))
            else:
                detail = "The area is within the NC-2 4,000 sq ft use-size threshold. Confirm the other use and project rules with Planning."
            steps.insert(1, PermitStep(title="NC-2 gross floor area check", detail=detail, url=USE_SIZE))
        steps.append(PermitStep(
            title="Public Health plan review",
            detail="Check the retail food facility plan review process for a new grocery store.",
            url=HEALTH,
        ))

    return PermitChecklist(
        parcel_id=parcel.id, category_id=proposal.category_id, zoning_districts=districts,
        special_use_districts=overlays, zoning_note=note, steps=steps,
    )
