import gzip
from functools import cache
from pathlib import Path

from shapely import contains_xy, from_wkb, prepare, transform
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .field import Grid

BOUNDARY_FILE = Path(__file__).parent / "data" / "mainland_gcj02.wkb.gz"


@cache
def mainland_boundary() -> BaseGeometry:
    with gzip.open(BOUNDARY_FILE, "rb") as source:
        boundary = from_wkb(source.read())
    prepare(boundary)
    return boundary


def inside_mainland(lng: float, lat: float) -> bool:
    # Match the six decimal places actually sent to Amap's distance endpoint.
    return bool(contains_xy(mainland_boundary(), round(lng, 6), round(lat, 6)))


def local_mainland(grid: Grid) -> BaseGeometry:
    extent = grid.params.radius_m + 2 * grid.params.spacing_m
    bounds = box(*grid.coordinates(-extent, -extent), *grid.coordinates(extent, extent))
    clipped = mainland_boundary().intersection(bounds)
    return transform(clipped, grid.local_meters, interleaved=False)
