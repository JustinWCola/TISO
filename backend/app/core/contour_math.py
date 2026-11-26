from shapely import hausdorff_distance
from shapely.geometry import LineString, MultiLineString

Vertex = tuple[float, float, float]
Facet = tuple[Vertex, Vertex, Vertex]


def crossing(a: Vertex, b: Vertex, level: float) -> Vertex:
    if a[2] > b[2]:
        a, b = b, a
    fraction = (level - a[2]) / (b[2] - a[2])
    return a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]), level


def contour_segment(facet: Facet, level: float) -> LineString | None:
    crossings = set()
    for a, b in zip(facet, facet[1:] + facet[:1]):
        if a[2] == level:
            crossings.add(a[:2])
        if min(a[2], b[2]) < level < max(a[2], b[2]):
            crossings.add(crossing(a, b, level)[:2])
    return LineString(sorted(crossings)) if len(crossings) == 2 else None


def contour_displacement(coarse: Facet, finer: list[Facet], levels: list[int]) -> float:
    durations = [vertex[2] for facet in finer for vertex in facet]
    low, high = min(durations), max(durations)
    error = 0.0
    for level in levels:
        if not low <= level <= high:
            continue
        original = contour_segment(coarse, level)
        segments = [line for facet in finer if (line := contour_segment(facet, level)) is not None]
        if original is None and not segments:
            continue
        if original is None or not segments:
            # A contour appeared or disappeared after sampling.
            return float("inf")
        error = max(error, float(hausdorff_distance(original, MultiLineString(segments), densify=0.25)))
    return error
