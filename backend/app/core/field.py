import math
from collections.abc import Iterable
from dataclasses import dataclass

import contourpy
import numpy as np
from shapely.geometry import LineString, Point as GeometryPoint, Polygon, box, mapping
from shapely.geometry.polygon import orient
from shapely.validation import make_valid

from app.providers.base import Point, TravelResult
from app.schemas import Parameters

METRES_PER_DEGREE = math.pi * 6_371_008.8 / 180
GeometryResult = tuple[dict, dict, float, bool, int]


@dataclass(frozen=True)
class Grid:
    params: Parameters

    @property
    def extent(self) -> int:
        return math.ceil(self.params.radius_m / self.params.spacing_m) + 1

    def coordinates(self, x: float, y: float) -> tuple[float, float]:
        center = self.params.destination
        longitude_scale = METRES_PER_DEGREE * math.cos(math.radians(center.lat))
        return center.lng + x / longitude_scale, center.lat + y / METRES_PER_DEGREE

    def nodes(self) -> Iterable[tuple[int, int, Point]]:
        spacing = self.params.spacing_m
        # One cell of padding in both axes allows clipping across the circular edge.
        buffer = self.params.radius_m + math.sqrt(2) * spacing
        for row in range(-self.extent, self.extent + 1):
            for col in range(-self.extent, self.extent + 1):
                x, y = col * spacing, row * spacing
                if math.hypot(x, y) <= buffer:
                    yield row, col, Point(f"{row}:{col}", *self.coordinates(x, y))


def parts(geometry, kind: str):
    if geometry.is_empty:
        return
    if geometry.geom_type == kind:
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from parts(part, kind)


def geometry_feature(geometry_type, coordinates, **properties):
    return {
        "type": "Feature",
        "geometry": {"type": geometry_type, "coordinates": coordinates},
        "properties": properties,
    }


def feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


def build_geometry(grid: Grid, durations: np.ndarray) -> GeometryResult:
    p = grid.params
    axis = np.arange(-grid.extent, grid.extent + 1) * p.spacing_m
    mask = ~np.isfinite(durations)
    generator = contourpy.contour_generator(
        x=axis, y=axis, z=np.ma.array(durations, mask=mask), corner_mask=False,
        fill_type="OuterOffset",
    )
    domain = GeometryPoint(0, 0).buffer(p.radius_m, quad_segs=64)
    max_seconds = p.max_duration_min * 60
    step = p.contour_interval_min * 60
    levels = [0, *range(step, max_seconds, step), max_seconds]
    lines, bands = [], []
    repairs = 0
    for minimum, maximum in zip(levels, levels[1:]):
        segments = []
        for vertices in generator.lines(maximum):
            if len(vertices) >= 2:
                segments.extend(parts(LineString(vertices).intersection(domain), "LineString"))
        if segments:
            lines.append(geometry_feature("MultiLineString", [
                [grid.coordinates(x, y) for x, y in line.coords] for line in segments
            ], duration_min=maximum / 60))

        rings_list, offsets_list = generator.filled(-1 if minimum == 0 else minimum, maximum)
        polygons = []
        for vertices, offsets in zip(rings_list, offsets_list, strict=True):
            rings = [vertices[a:b] for a, b in zip(offsets, offsets[1:])]
            polygon = Polygon(rings[0], rings[1:])
            if not polygon.is_valid:
                polygon = make_valid(polygon)
                repairs += 1
            polygons.extend(parts(polygon.intersection(domain), "Polygon"))
        if polygons:
            bands.append(geometry_feature("MultiPolygon", [
                [[grid.coordinates(x, y) for x, y in ring] for ring in
                 mapping(orient(polygon, sign=1))["coordinates"]]
                for polygon in polygons
            ], minimum_min=minimum / 60, maximum_min=maximum / 60))

    # Estimate known area from whole supported cells clipped to the analysis circle.
    coverage = 0.0
    valid_cells = ~(mask[1:, 1:] | mask[:-1, 1:] | mask[1:, :-1] | mask[:-1, :-1])
    for row, col in zip(*np.nonzero(valid_cells)):
        cell = box(axis[col], axis[row], axis[col + 1], axis[row + 1])
        coverage += cell.intersection(domain).area
    radius = np.hypot(axis[None, :], axis[:, None])
    outer = (radius <= p.radius_m) & (radius >= p.radius_m - p.spacing_m)
    truncated = bool(np.any(outer & (durations <= max_seconds)))
    return feature_collection(lines), feature_collection(bands), min(1.0, coverage / domain.area), truncated, repairs


def sample_record(point: Point, result: TravelResult) -> dict:
    return {
        "id": point.id, "coordinates": [point.lon, point.lat],
        "duration_s": result.duration_s, "status": result.status,
        "observed_at": result.observed_at.isoformat(),
    }


def sample_payload(points: Iterable[Point], results: dict[str, TravelResult], geometry: GeometryResult) -> dict:
    samples = []
    counts = {"ok": 0, "unreachable": 0, "error": 0}
    for point in points:
        result = results[point.id]
        counts[result.status] += 1
        samples.append(sample_record(point, result))

    contours, bands, coverage, truncated, repairs = geometry
    return {
        "samples": samples, "contours": contours, "bands": bands,
        "coverage": coverage, "boundary_truncated": truncated, "geometry_repairs": repairs,
        "sample_count": len(samples), "success_count": counts["ok"],
        "unreachable_count": counts["unreachable"], "error_count": counts["error"],
    }
