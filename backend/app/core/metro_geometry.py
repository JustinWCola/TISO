import numpy as np
from shapely import buffer, points as geometry_points, simplify, union_all
from shapely.geometry import Point as GeometryPoint, Polygon, mapping
from shapely.geometry.polygon import orient

from app.providers.base import Point, TravelResult

from .field import Grid, feature_collection, geometry_feature, parts, sample_payload
from .mainland import local_mainland

SIMPLIFICATION_M = 5


def metro_geometry(grid: Grid, points: list[Point], results: dict[str, TravelResult]) -> dict:
    params = grid.params
    radius = GeometryPoint(0, 0).buffer(params.radius_m, quad_segs=64)
    domain = radius.intersection(local_mainland(grid))
    origins = [(grid.local_meters(point.lon, point.lat), results[point.id].duration_s)
               for point in points if results[point.id].status == "ok" and results[point.id].duration_s is not None]
    centers = geometry_points([coordinate for coordinate, _ in origins]) if origins else np.empty(0, dtype=object)
    leads = np.array([lead for _, lead in origins], dtype=float)
    maximum = params.max_duration_min * 60
    step = params.contour_interval_min * 60
    contours, bands = [], []
    previous = Polygon()
    truncated = False
    minimum = 0
    for limit in [*range(step, maximum, step), maximum]:
        eligible = leads < limit
        walk = union_all(buffer(centers[eligible], (limit - leads[eligible]) * params.walk_speed_m_s, quad_segs=24))
        if walk.is_empty:
            minimum = limit
            continue
        if limit == maximum:
            truncated = not radius.covers(walk)
        drawing = simplify(walk, SIMPLIFICATION_M, preserve_topology=True)
        current = drawing.intersection(domain)
        outline = drawing.boundary.intersection(domain)
        lines = [[grid.coordinates(x, y) for x, y in line.coords] for line in parts(outline, "LineString")]
        if lines:
            contours.append(geometry_feature("MultiLineString", lines, duration_min=limit / 60))

        band = current.difference(previous)
        polygons = [[[grid.coordinates(x, y) for x, y in ring] for ring in
                     mapping(orient(polygon, sign=1))["coordinates"]] for polygon in parts(band, "Polygon")]
        if polygons:
            bands.append(geometry_feature("MultiPolygon", polygons,
                                          minimum_min=minimum / 60,
                                          maximum_min=limit / 60))
        previous = current
        minimum = limit
    return sample_payload(points, results, (
        feature_collection(contours), feature_collection(bands),
        previous.area / domain.area if domain.area else 0.0, truncated, 0,
    ))
