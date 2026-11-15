from math import sqrt

from pyproj import Transformer
from shapely import affinity
from shapely.geometry import mapping, shape
from shapely.ops import transform

from .schemas import Massing, MultiPolygon, ParcelFeature, Polygon

TO_METERS = Transformer.from_crs(4326, 26910, always_xy=True)
TO_WGS84 = Transformer.from_crs(26910, 4326, always_xy=True)


def validate_bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = (float(value) for value in bbox.split(","))
    except ValueError:
        raise ValueError("Expected four comma-separated WGS84 coordinates") from None
    if not (
        -180 <= west < east <= 180 and -90 <= south < north <= 90
        and east - west <= 0.5 and north - south <= 0.5
    ):
        raise ValueError("Expected ordered bounds spanning at most 0.5° per axis")
    return west, south, east, north


def validate_wgs84(geometry):
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise ValueError("Expected nonempty, valid 2D geometry")
    west, south, east, north = geometry.bounds
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("Geometry falls outside WGS84 bounds")
    return geometry


def polygon_model(geometry):
    if geometry.geom_type == "Polygon":
        return Polygon.model_validate(mapping(geometry))
    if geometry.geom_type == "MultiPolygon":
        return MultiPolygon.model_validate(mapping(geometry))
    raise ValueError("Expected a Polygon or MultiPolygon")


def project(geometry):
    return transform(TO_METERS.transform, geometry)


def unproject(geometry):
    return transform(TO_WGS84.transform, geometry)


def parcel_shape(parcel: ParcelFeature):
    return shape(parcel.geometry.model_dump())


def massing(parcel: ParcelFeature) -> Massing:
    parcel_geom = parcel_shape(parcel)
    polygon = project(parcel_geom)
    origin = polygon.representative_point()
    footprint = affinity.scale(polygon, xfact=sqrt(0.5), yfact=sqrt(0.5), origin=origin)
    footprint_wgs84 = unproject(footprint.intersection(polygon)).intersection(parcel_geom)
    if (
        footprint_wgs84.is_empty
        or not footprint_wgs84.is_valid
        or footprint_wgs84.geom_type not in {"Polygon", "MultiPolygon"}
    ):
        return Massing(geometry=None, reason="footprint_unavailable")
    return Massing(geometry=polygon_model(footprint_wgs84))
