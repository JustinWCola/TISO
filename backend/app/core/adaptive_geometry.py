from bisect import bisect_left, bisect_right
from collections import defaultdict

from shapely import intersection, line_merge, union_all
from shapely.geometry import Point as GeometryPoint, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from app.providers.base import TravelResult

from .contour_math import Facet, Vertex, contour_segment, crossing
from .field import GeometryResult, feature_collection, geometry_feature, parts, sample_payload
from .hex_sampling import MIN_SPACING_M, HexSampler, Node, edges, meters, side

PRECISION_M = 0.001


def known_facets(sampler: HexSampler, results: dict[str, TravelResult]) -> list[Facet]:
    measurements = sampler.measurements(results)
    q_lines, r_lines, diagonals = defaultdict(list), defaultdict(list), defaultdict(list)
    for q, r in measurements:
        q_lines[q].append(r)
        r_lines[r].append(q)
        diagonals[q + r].append(q)
    for lines in (q_lines, r_lines, diagonals):
        for line in lines.values():
            line.sort()

    def between(line: list[int], start: int, end: int) -> list[int]:
        low, high = sorted((start, end))
        values = line[bisect_left(line, low):bisect_right(line, high)]
        return values[:-1] if start < end else values[:0:-1]

    def edge_nodes(a: Node, b: Node) -> list[Node]:
        q, r = a
        if q == b[0]:
            return [(q, y) for y in between(q_lines[q], r, b[1])]
        if r == b[1]:
            return [(x, r) for x in between(r_lines[r], q, b[0])]
        diagonal = q + r
        return [(x, diagonal - x) for x in between(diagonals[diagonal], q, b[0])]

    facets = []
    for cell in sorted(sampler.leaves):
        probe = sampler.probes.get(cell)
        if probe in measurements:
            depth = measurements[probe]
            if depth is None:
                continue
            center = (*meters(probe), depth)
        else:
            values = [measurements[node] for node in cell]
            if None in values:
                continue
            coords = [meters(node) for node in cell]
            center = (sum(x for x, _ in coords) / 3, sum(y for _, y in coords) / 3, sum(values) / 3)
        # Shared edge nodes include hanging vertices and recorded failures on both sides.
        border = [node for a, b in edges(cell) for node in edge_nodes(a, b)]
        for a, b in zip(border, border[1:] + border[:1]):
            da, db = measurements[a], measurements[b]
            if da is not None and db is not None:
                facets.append((center, (*meters(a), da), (*meters(b), db)))
    return facets


def clip(vertices: list[Vertex], level: float, *, below: bool) -> list[Vertex]:
    result = []
    previous = vertices[-1]
    previous_in = previous[2] <= level if below else previous[2] >= level
    for vertex in vertices:
        inside = vertex[2] <= level if below else vertex[2] >= level
        if previous_in != inside:
            result.append(crossing(previous, vertex, level))
        if inside:
            result.append(vertex)
        previous, previous_in = vertex, inside
    return result


def build_adaptive_geometry(
    sampler: HexSampler, facets: list[Facet], mainland: BaseGeometry | None = None,
) -> GeometryResult:
    grid = sampler.grid
    domain = (union_all([Polygon([meters(node) for node in cell]) for cell in sampler.leaves], grid_size=PRECISION_M)
              if sampler.expanding
              else GeometryPoint(0, 0).buffer(grid.params.radius_m, quad_segs=64))
    land = intersection(domain, mainland, grid_size=PRECISION_M) if mainland is not None else domain
    support = union_all([Polygon([point[:2] for point in facet]) for facet in facets], grid_size=PRECISION_M)
    coverage = min(1.0, support.intersection(land).area / land.area) if land.area else 0.0
    ranges = [(min(a[2] for a in facet), max(a[2] for a in facet), facet) for facet in facets]
    contours, bands = [], []
    truncated = not sampler.frontier_closed
    boundary = domain.boundary.buffer(2 * PRECISION_M) if sampler.expanding else domain.boundary
    for minimum, maximum in zip([0, *sampler.levels[:-1]], sampler.levels):
        segments, polygons = [], []
        for low, high, facet in ranges:
            if low <= maximum <= high and low != high:
                segment = contour_segment(facet, maximum)
                if segment is not None:
                    segments.append(segment)
            if low > maximum or (minimum > 0 and high <= minimum):
                continue
            vertices = list(facet)
            if minimum > low:
                vertices = clip(vertices, minimum, below=False)
            if vertices and maximum < high:
                vertices = clip(vertices, maximum, below=True)
            if len(vertices) >= 3:
                polygon = Polygon([vertex[:2] for vertex in vertices])
                if polygon.area > 0:
                    polygons.append(polygon)

        if segments:
            stitched = intersection(union_all(segments, grid_size=PRECISION_M), land, grid_size=PRECISION_M)
            lines = [[grid.coordinates(x, y) for x, y in line.coords] for line in parts(line_merge(stitched), "LineString")]
            if lines:
                contours.append(geometry_feature("MultiLineString", lines, duration_min=maximum / 60))
        if polygons:
            stitched = union_all(polygons, grid_size=PRECISION_M)
            clipped = intersection(stitched, land, grid_size=PRECISION_M)
            truncated |= clipped.intersects(boundary)
            rings = [
                [[grid.coordinates(x, y) for x, y in ring] for ring in mapping(orient(polygon, sign=1))["coordinates"]]
                for polygon in parts(clipped, "Polygon")
            ]
            if rings:
                bands.append(geometry_feature("MultiPolygon", rings, minimum_min=minimum / 60, maximum_min=maximum / 60))
    return feature_collection(contours), feature_collection(bands), coverage, truncated, 0


def build_adaptive_result(
    sampler: HexSampler, results: dict[str, TravelResult], mainland: BaseGeometry | None = None,
) -> dict:
    facets = known_facets(sampler, results)
    nodes = [point for point in sampler.points.values() if point.id in results]
    return {
        **sample_payload(nodes, results, build_adaptive_geometry(sampler, facets, mainland)),
        "minimum_spacing_m": min((side(cell) * MIN_SPACING_M for cell in sampler.leaves),
                                 default=sampler.grid.params.spacing_m),
        "refinement_rounds": sampler.rounds,
    }
