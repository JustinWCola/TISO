import math
from collections.abc import Awaitable, Callable, Iterator, Set
from dataclasses import dataclass

from app.providers.base import Point, TravelResult

from .contour_math import Facet, contour_displacement
from .field import Grid

MIN_SPACING_M = 250
CONTOUR_TOLERANCE_M = 50
MIDPOINT_TOLERANCE_S = 45
MAX_REFINEMENT_ROUNDS = 4
INNER_RINGS = 2
OUTER_STEP_MULTIPLIER = 2
FRONTIER_BUFFER_S = 5 * 60
ROW_HEIGHT_M = MIN_SPACING_M * math.sqrt(3) / 2
Node = tuple[int, int]
Triangle = tuple[Node, Node, Node]
Measurements = dict[Node, float | None]
AXIAL_DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))


def meters(node: Node) -> tuple[float, float]:
    q, r = node
    return (q + r / 2) * MIN_SPACING_M, r * ROW_HEIGHT_M


def seed_nodes(grid: Grid) -> Iterator[Node]:
    spacing = grid.params.spacing_m
    step = spacing // MIN_SPACING_M
    buffer = grid.params.radius_m + spacing
    row_height = step * ROW_HEIGHT_M
    last_row = math.floor(buffer / row_height)
    for row in range(-last_row, last_row + 1):
        x_limit = math.sqrt(max(0.0, buffer**2 - (row * row_height)**2))
        first_column = math.ceil(-x_limit / spacing - row / 2 - 1e-9)
        last_column = math.floor(x_limit / spacing - row / 2 + 1e-9)
        for column in range(first_column, last_column + 1):
            yield column * step, row * step


def ring_nodes(index: int, step: int) -> Iterator[Node]:
    q, r = -index, index
    for dq, dr in AXIAL_DIRECTIONS:
        for _ in range(index):
            yield q * step, r * step
            q, r = q + dq, r + dr


def ring_radius(node: Node) -> int:
    q, r = node
    return max(abs(q), abs(r), abs(q + r))


def ring_sectors(index: int, step: int) -> list[list[Node]]:
    ring = list(ring_nodes(index, step))
    wrapped = [*ring, ring[0]]
    return [wrapped[start:start + index + 1] for start in range(0, len(ring), index)]


async def survey_outer(
    max_ring: int, step: int, duration_limit: int,
    visit: Callable[[list[Node]], Awaitable[bool]],
    measurements: Callable[[], Measurements],
) -> bool:
    safe_checks = [0] * 6
    next_probe = [2] * 6
    deferred: list[Node] = []
    for index in range(2, max_ring + 1):
        due = {side for side, next_ring in enumerate(next_probe) if index >= next_ring}
        if not due:
            continue
        sectors = ring_sectors(index, step)
        if not await visit([*deferred, *(node for side in sorted(due) for node in sectors[side])]):
            return False
        values = measurements()

        def near(node: Node) -> bool:
            value = values.get(node)
            return value is None or value <= duration_limit

        # A hit after a skipped ring needs its inner neighbors for contour interpolation.
        previous = ring_sectors(index - 1, step)
        backfill = {side for side in due if any(near(node) for node in sectors[side])}
        neighbors = set()
        for side in due:
            if near(sectors[side][0]):
                neighbors.add((side - 1) % 6)
            if near(sectors[side][-1]):
                neighbors.add((side + 1) % 6)
        reopened = neighbors - due
        deferred = [node for side in sorted(backfill | reopened) for node in previous[side] if node not in values]
        if reopened:
            # Reopened scouts affect this round's decision. Inner backfill can otherwise ride with the next round.
            if not await visit(deferred + [node for side in sorted(reopened) for node in sectors[side]]):
                return False
            deferred = []
            values = measurements()
        for side in due | reopened:
            safe_checks[side] = 0 if any(near(node) for node in sectors[side]) else safe_checks[side] + 1
            # Once two probes are safe, skip a ring before checking for disconnected pockets.
            next_probe[side] = index + min(3, max(1, safe_checks[side]))
        if all(count >= 3 for count in safe_checks):
            return await visit(deferred)
    await visit(deferred)
    return False


def cross(a: Node, b: Node, point: Node) -> int:
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def barycentric(cell: Triangle, point: Node) -> tuple[float, float, float]:
    a, b, c = cell
    area = cross(a, b, c)
    return cross(b, c, point) / area, cross(c, a, point) / area, cross(a, b, point) / area


def midpoint(a: Node, b: Node) -> Node:
    return (a[0] + b[0]) // 2, (a[1] + b[1]) // 2


def edges(cell: Triangle) -> tuple[tuple[Node, Node], ...]:
    a, b, c = cell
    return (a, b), (b, c), (c, a)


def measured_facet(cell: Triangle, measurements: Measurements) -> Facet | None:
    a, b, c = cell
    ta, tb, tc = (measurements.get(node) for node in cell)
    if ta is None or tb is None or tc is None:
        return None
    return (*meters(a), ta), (*meters(b), tb), (*meters(c), tc)


def side(cell: Triangle) -> int:
    return max(node[0] for node in cell) - min(node[0] for node in cell)


def interior_probe(cell: Triangle, minimum_step: int) -> Node | None:
    q = sum(node[0] for node in cell) / 3
    r = sum(node[1] for node in cell) / 3
    candidates = [
        (x * minimum_step, y * minimum_step)
        for x in (math.floor(q / minimum_step), math.ceil(q / minimum_step))
        for y in (math.floor(r / minimum_step), math.ceil(r / minimum_step))
        if all(cross(a, b, (x * minimum_step, y * minimum_step)) > 0 for a, b in edges(cell))
    ]
    return min(candidates, key=lambda node: (node[0] - q)**2 + (node[1] - r)**2
               + (node[0] - q) * (node[1] - r), default=None)


def intersects_radius(cell: Triangle, radius: float) -> bool:
    vertices = [meters(node) for node in cell]
    squared_radius = radius**2
    if any(x*x + y*y < squared_radius for x, y in vertices):
        return True
    if all(cross(a, b, (0, 0)) >= 0 for a, b in edges(cell)):
        return True
    for (x, y), (end_x, end_y) in zip(vertices, vertices[1:] + vertices[:1]):
        dx, dy = end_x - x, end_y - y
        fraction = max(0.0, min(1.0, -(x * dx + y * dy) / (dx*dx + dy*dy)))
        if (x + fraction * dx)**2 + (y + fraction * dy)**2 < squared_radius:
            return True
    return False


@dataclass(frozen=True)
class Refinement:
    points: list[Point]
    cells: list[Triangle]


class HexSampler:
    """Task-local hexagonal lattice with midpoint refinement of its triangular facets."""

    def __init__(self, grid: Grid, *, expanding: bool = False) -> None:
        self.grid = grid
        self.expanding = expanding
        self.frontier_closed = not expanding
        self.seed_step = grid.params.spacing_m // MIN_SPACING_M
        self.minimum_step = grid.params.min_spacing_m // MIN_SPACING_M
        self.points: dict[Node, Point] = {}
        self.leaves: set[Triangle] = set()
        self.probes: dict[Triangle, Node] = {}
        self.contour_errors: dict[Triangle, float] = {}
        self.rounds = 0
        end = grid.params.max_duration_min * 60
        self.levels = [*range(grid.params.contour_interval_min * 60, end, grid.params.contour_interval_min * 60), end]
        for node in ((0, 0),) if expanding else seed_nodes(grid):
            self.add_point(node)
        if not expanding:
            self.seed_mesh(self.points.keys())

    def seed_mesh(self, nodes: Set[Node], *, step: int | None = None, outside: int = 0) -> None:
        step = step or self.seed_step
        anchors = nodes | {(q - step, r - step) for q, r in nodes}
        for q, r in anchors:
            a, b, c, d = (q, r), (q + step, r), (q, r + step), (q + step, r + step)
            for cell in ((a, b, c), (d, c, b)):
                if (all(node in nodes for node in cell) and any(ring_radius(node) > outside for node in cell)
                        and self.in_domain(cell)):
                    self.leaves.add(cell)

    def seed_expanded_mesh(self, results: dict[str, TravelResult]) -> None:
        nodes = self.measurements(results)
        inner_radius = INNER_RINGS * self.seed_step
        coarse_step = OUTER_STEP_MULTIPLIER * self.seed_step
        self.seed_mesh({node for node in nodes if ring_radius(node) <= inner_radius})
        self.seed_mesh(
            {(q, r) for q, r in nodes if q % coarse_step == r % coarse_step == 0},
            step=coarse_step, outside=inner_radius,
        )

    def in_domain(self, cell: Triangle) -> bool:
        return self.expanding or intersects_radius(cell, self.grid.params.radius_m)

    def add_point(self, node: Node) -> Point:
        point = self.points.get(node)
        if point is None:
            q, r = node
            point = Point(f"{r}:{q}", *self.grid.coordinates(*meters(node)))
            self.points[node] = point
        return point

    def measurements(self, results: dict[str, TravelResult]) -> Measurements:
        return {
            node: results[point.id].duration_s if results[point.id].status == "ok" else None
            for node, point in self.points.items() if point.id in results
        }

    def priority(self, cell: Triangle, measurements: Measurements) -> tuple[int, bool, float]:
        values = [measurements[node] for node in cell]
        valid = [value for value in values if value is not None]
        probe = self.probes.get(cell)
        measured_probe = probe is not None and probe in measurements
        center = measurements[probe] if measured_probe else None
        comparison = valid + ([center] if center is not None else [])
        unknown_edge = len(valid) < len(values) or (measured_probe and center is None)
        if not comparison:
            return 0, False, 0.0
        low, high = min(comparison), max(comparison)
        variation = high - low
        if low < high and any(low <= level <= high for level in self.levels):
            return 4, unknown_edge, variation
        if unknown_edge:
            return 3, True, float(len(comparison))
        if self.expanding and side(cell) > self.seed_step and low <= self.levels[-1] + FRONTIER_BUFFER_S:
            return 3, False, variation
        if variation > 600:
            return 2, False, variation
        if center is not None:
            predicted = sum(value * weight for value, weight in zip(valid, barycentric(cell, probe)))
            error = abs(center - predicted)
            if error > 180:
                return 1, False, error
        return 0, False, 0.0

    def shared_edge_changed(self, cell: Triangle, measurements: Measurements) -> bool:
        stride = side(cell)
        border = []
        for (q, r), (end_q, end_r) in edges(cell):
            dq, dr = (end_q - q) // stride, (end_r - r) // stride
            border.extend(node for offset in range(0, stride, self.minimum_step)
                          if (node := (q + offset * dq, r + offset * dr)) in measurements)
        if len(border) == 3:
            return False
        original = measured_facet(cell, measurements)
        if original is None or any(measurements[node] is None for node in border):
            return True
        center = tuple(sum(axis) / 3 for axis in zip(*original))
        vertices = [(*meters(node), measurements[node]) for node in border]
        finer = [(center, a, b) for a, b in zip(vertices, vertices[1:] + vertices[:1])]
        return contour_displacement(original, finer, self.levels) > CONTOUR_TOLERANCE_M

    def plan(self, results: dict[str, TravelResult], limit: int, *, batch_size: int) -> Refinement:
        # Leave room for later rounds without intentionally breaking up a query batch.
        if self.rounds < MAX_REFINEMENT_ROUNDS - 1 and limit > 2 * batch_size:
            limit = max(batch_size, limit * 2 // (3 * batch_size) * batch_size)
        measurements = self.measurements(results)
        marked, unchecked = [], []
        for cell in sorted(self.leaves):
            if side(cell) < 2 * self.minimum_step:
                continue
            error = self.contour_errors.get(cell)
            if self.expanding and all(
                (value := measurements[node]) is not None and value > self.levels[-1] + FRONTIER_BUFFER_S
                for node in cell
            ) and cell not in self.probes:
                continue
            priority, unknown_edge, score = self.priority(cell, measurements)
            if error is not None and error <= CONTOUR_TOLERANCE_M:
                if self.shared_edge_changed(cell, measurements):
                    priority = max(priority, 4)
                elif priority == 4:
                    continue
            if priority:
                position_error = error if error is not None and error > CONTOUR_TOLERANCE_M else 0.0
                marked.append((-priority, not unknown_edge, -position_error, -score, -side(cell), cell))
            elif cell not in self.probes and all(measurements[node] is not None for node in cell):
                candidate = interior_probe(cell, self.minimum_step)
                if candidate is not None and candidate not in measurements:
                    unchecked.append((cell, candidate))

        # Spread bounded independent checks through the region rather than favoring one corner.
        reserve = min(16, limit // 12, len(unchecked)) if self.rounds < MAX_REFINEMENT_ROUNDS - 1 else 0
        pending: dict[Node, Point] = {}
        selected = []
        for _, _, _, _, _, cell in sorted(marked):
            required = [midpoint(a, b) for a, b in edges(cell)]
            missing = [node for node in required if node not in measurements and node not in pending]
            if len(pending) + len(missing) > limit - reserve:
                continue
            selected.append(cell)
            for node in missing:
                pending[node] = self.add_point(node)
        for index in range(reserve):
            cell, probe = unchecked[(2 * index + 1) * len(unchecked) // (2 * reserve)]
            self.probes[cell] = probe
            pending[probe] = self.add_point(probe)
        return Refinement(list(pending.values()), selected)

    def apply(self, plan: Refinement, results: dict[str, TravelResult]) -> None:
        self.rounds += 1
        measurements = self.measurements(results)
        for cell in plan.cells:
            ab, bc, ca = (midpoint(a, b) for a, b in edges(cell))
            if not all(node in measurements for node in (ab, bc, ca)):
                continue
            a, b, c = cell
            self.leaves.remove(cell)
            probe = self.probes.pop(cell, None)
            previous_error = self.contour_errors.pop(cell, None)
            children = ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))
            original = measured_facet(cell, measurements)
            finer = [facet for child in children if (facet := measured_facet(child, measurements)) is not None]
            # An independent interior measurement is extra evidence that must survive the split.
            error = (contour_displacement(original, finer, self.levels)
                     if original is not None and len(finer) == 4 and probe is None else None)
            # On a curved field, agreement along two edges can hide a bend inside the triangle.
            if error is not None and error <= CONTOUR_TOLERANCE_M and any(
                abs(measurements[midpoint(start, end)] - (measurements[start] + measurements[end]) / 2)
                > MIDPOINT_TOLERANCE_S for start, end in edges(cell)
            ):
                error = None
            # Check a previously unstable contour once more before stopping.
            needs_confirmation = (
                error is not None and error <= CONTOUR_TOLERANCE_M
                and previous_error is not None and previous_error > CONTOUR_TOLERANCE_M
            )
            for child in children:
                if not self.in_domain(child):
                    continue
                self.leaves.add(child)
                if error is not None and not needs_confirmation:
                    self.contour_errors[child] = error
                if probe is not None and all(cross(start, end, probe) > 0 for start, end in edges(child)):
                    self.probes[child] = probe
