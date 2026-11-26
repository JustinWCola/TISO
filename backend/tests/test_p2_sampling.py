import asyncio
import math
from datetime import UTC, datetime

import pytest
from shapely.geometry import Point as GeometryPoint
from shapely.geometry import shape
from shapely.ops import unary_union

from app.core.adaptive_geometry import build_adaptive_result, known_facets
from app.core.field import Grid
from app.core.hex_sampling import HexSampler, meters, ring_radius, ring_sectors, seed_nodes, survey_outer
from app.providers.base import TravelResult
from app.schemas import Parameters

DESTINATION = {"lng": 121.4737, "lat": 31.2304}
OBSERVED = datetime(2026, 11, 25, tzinfo=UTC)


def seed(radius: int = 4000) -> HexSampler:
    return HexSampler(Grid(Parameters(destination=DESTINATION, radius_m=radius, spacing_m=1000)))


def sample(sampler, points, results, field):
    positions = {point.id: node for node, point in sampler.points.items()}
    for point in points:
        duration = field(*meters(positions[point.id]))
        results[point.id] = TravelResult(
            point.id, duration, None, "error" if duration is None else "ok", None, OBSERVED,
        )


def test_seed_has_six_neighbors_and_refinement_shares_boundary_nodes():
    sampler = seed()
    nodes = list(seed_nodes(sampler.grid))
    neighbors = {node for node in nodes if math.isclose(math.hypot(*meters(node)), 1000)}
    assert neighbors == {(4, 0), (0, 4), (-4, 4), (-4, 0), (0, -4), (4, -4)}

    results = {}
    field = lambda x, y: 1500 + x * 0.2 + y * 0.1
    sample(sampler, sampler.points.values(), results, field)
    first = sampler.plan(results, 18, batch_size=20)
    assert first.cells and 0 < len(first.points) <= 18
    before = set(results)
    assert len({point.id for point in first.points}) == len(first.points)
    assert before.isdisjoint(point.id for point in first.points)
    sample(sampler, first.points, results, field)
    sampler.apply(first, results)
    second = sampler.plan(results, 18, batch_size=20)
    assert set(results).isdisjoint(point.id for point in second.points)
    facets = known_facets(sampler, results)
    for facet in facets:
        for x, y, value in facet:
            assert value == pytest.approx(field(x, y))

    result = build_adaptive_result(sampler, results)
    assert result["coverage"] == pytest.approx(1.0, abs=1e-5)
    assert result["minimum_spacing_m"] == 500
    bands = [shape(feature["geometry"]) for feature in result["bands"]["features"]]
    assert bands and all(band.is_valid for band in bands)
    for index, band in enumerate(bands):
        assert all(band.intersection(other).area < 1e-15 for other in bands[index + 1:])


def test_missing_hex_sample_remains_unpainted():
    sampler = seed()
    missing = sampler.points[(4, 0)]
    results = {}
    sample(sampler, sampler.points.values(), results, lambda x, y: 900 if x != 1000 or y != 0 else None)
    result = build_adaptive_result(sampler, results)
    area = unary_union([shape(feature["geometry"]) for feature in result["bands"]["features"]])
    assert 0 < result["coverage"] < 1
    assert result["error_count"] == 1
    assert not area.covers(GeometryPoint(missing.lon, missing.lat))
    assert area.covers(GeometryPoint(*sampler.grid.coordinates(-2000, 0)))


def test_unflagged_hex_region_is_probed_and_hidden_gradient_gets_refined():
    sampler = seed(radius=7000)
    results = {}
    sample(sampler, sampler.points.values(), results, lambda _x, _y: 2000)
    probe_round = sampler.plan(results, 48, batch_size=20)
    assert probe_round.points and not probe_round.cells
    assert len(probe_round.points) <= 4
    sample(sampler, probe_round.points, results, lambda _x, _y: 700)
    sampler.apply(probe_round, results)
    refine = sampler.plan(results, 48, batch_size=20)
    assert refine.cells and len(refine.points) <= 48


def test_independent_backfill_and_next_scouts_share_one_request():
    measurements = {}
    requests = []
    reachable = ring_sectors(5, 8)[0][2]

    async def visit(nodes):
        pending = list(dict.fromkeys(node for node in nodes if node not in measurements))
        for start in range(0, len(pending), 20):
            batch = pending[start:start + 20]
            requests.append(batch)
            measurements.update((node, 600 if node == reachable else 1500) for node in batch)
        return True

    assert asyncio.run(survey_outer(10, 8, 1200, visit, lambda: measurements))
    shared = [batch for batch in requests if {32, 48} <= {ring_radius(node) for node in batch}]
    assert len(shared) == 1
    assert {ring_radius(node) for node in shared[0]} == {32, 48}
    assert set(ring_sectors(4, 8)[0] + ring_sectors(6, 8)[0]) == set(shared[0])


@pytest.mark.parametrize("bent", [False, True])
def test_refinement_continues_only_when_midpoints_shift_contour(bent):
    grid = Grid(Parameters(destination=DESTINATION, radius_m=4000, spacing_m=2000, max_duration_min=15))
    sampler = HexSampler(grid, expanding=True)
    cell = ((0, 0), (8, 0), (0, 8))
    sampler.leaves = {cell}
    results = {}

    def field(x, _y):
        return 0.9 * x + (0.6 * min(x, 2000 - x) if bent else 0)

    def contour_x():
        coordinates = build_adaptive_result(sampler, results)["contours"]["features"][0]["geometry"]["coordinates"]
        west = grid.coordinates(0, 0)[0]
        x, _ = min((point for line in coordinates for point in line), key=lambda point: point[1])
        return (x - west) / (grid.coordinates(1, 0)[0] - west)

    sample(sampler, [sampler.add_point(node) for node in cell], results, field)
    assert contour_x() == pytest.approx(1000, abs=0.01)
    first = sampler.plan(results, 20, batch_size=20)
    assert len(first.points) == 3
    sample(sampler, first.points, results, field)
    sampler.apply(first, results)
    assert contour_x() == pytest.approx(600 if bent else 1000, abs=0.01)
    second = sampler.plan(results, 20, batch_size=20)
    assert bool(second.points) is bent
