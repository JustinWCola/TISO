"""Compare adaptive hex sampling with equal-budget square and dense offline references."""

import json
import math
from datetime import UTC, datetime
from time import perf_counter

import numpy as np
from shapely import STRtree, points
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from app.core.adaptive_geometry import build_adaptive_geometry, known_facets
from app.core.field import Grid, build_geometry
from app.core.hex_sampling import MAX_REFINEMENT_ROUNDS, HexSampler, meters
from app.providers.base import DRIVING_CAPABILITIES, TravelResult
from app.schemas import Parameters

BATCH = DRIVING_CAPABILITIES.max_origins
DESTINATION = {"lng": 121.4737, "lat": 31.2304}
OBSERVED = datetime(2026, 11, 25, tzinfo=UTC)
RADIUS = 8000


def anisotropic(x, y):
    return 0.27 * np.hypot(1.18 * x, 0.88 * y)


def corridor(x, y):
    discount = 1 - 0.7 * np.exp(-((y - 0.3 * x - 680) / 610) ** 2)
    return 0.39 * np.hypot(x, y) * discount


def barriers(x, y):
    values = 0.36 * np.hypot(x, y)
    blocked = np.hypot(x + 2300, y - 850) < 1100
    return np.where(blocked | ((x > 1700) & (np.abs(y + 2100) < 430)), np.nan, values)


def regular(grid, field):
    axis = np.arange(-grid.extent, grid.extent + 1) * grid.params.spacing_m
    values = np.full((len(axis), len(axis)), np.nan)
    nodes = list(grid.nodes())
    for row, col, _ in nodes:
        values[row + grid.extent, col + grid.extent] = field(col * grid.params.spacing_m, row * grid.params.spacing_m)
    return axis, values, len(nodes)


def square_predictions(axis, values, holdouts):
    x, y = holdouts.T
    step = axis[1] - axis[0]
    col = np.floor((x - axis[0]) / step).astype(int)
    row = np.floor((y - axis[0]) / step).astype(int)
    u, v = (x - axis[col]) / step, (y - axis[row]) / step
    return ((1-u) * (1-v) * values[row, col] + u * (1-v) * values[row, col+1]
            + (1-u) * v * values[row+1, col] + u * v * values[row+1, col+1])


def facet_predictions(facets, holdouts):
    tree = STRtree([Polygon([vertex[:2] for vertex in facet]) for facet in facets])
    indices, hits = tree.query(points(holdouts), predicate="intersects")
    values = np.full(len(holdouts), np.nan)
    if not len(indices):
        return values
    a, b, c = np.asarray(facets)[hits].transpose(1, 0, 2)
    u, v, delta = b[:, :2] - a[:, :2], c[:, :2] - a[:, :2], holdouts[indices] - a[:, :2]
    denominator = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
    wu = (delta[:, 0] * v[:, 1] - delta[:, 1] * v[:, 0]) / denominator
    wv = (u[:, 0] * delta[:, 1] - u[:, 1] * delta[:, 0]) / denominator
    values[indices] = a[:, 2] + wu * (b[:, 2] - a[:, 2]) + wv * (c[:, 2] - a[:, 2])
    return values


def adaptive(grid, field, max_points, max_batches):
    sampler = HexSampler(grid)
    results = {}

    def observe(points_to_sample):
        key_to_node = {point.id: node for node, point in sampler.points.items()}
        for point in points_to_sample:
            duration = float(field(*meters(key_to_node[point.id])))
            valid = math.isfinite(duration)
            results[point.id] = TravelResult(
                point.id, duration if valid else None, None, "ok" if valid else "error", None, OBSERVED,
            )

    observe(sampler.points.values())
    batches = math.ceil((len(results) - 1) / BATCH)
    for _ in range(MAX_REFINEMENT_ROUNDS):
        limit = min(max_points - len(results), (max_batches - batches) * BATCH)
        if limit <= 0:
            break
        plan = sampler.plan(results, limit, batch_size=BATCH)
        if not plan.points and not plan.cells:
            break
        observe(plan.points)
        batches += math.ceil(len(plan.points) / BATCH)
        sampler.apply(plan, results)
    return sampler, known_facets(sampler, results), len(results), batches


def iou(bands, reference):
    def cumulative(collection, limit):
        return unary_union([shape(feature["geometry"]) for feature in collection["features"]
                            if feature["properties"]["maximum_min"] <= limit])

    values = []
    for level in (15, 30, 45, 60):
        area, target = cumulative(bands, level), cumulative(reference, level)
        combined = area.union(target).area
        if combined:
            values.append(area.intersection(target).area / combined)
    return float(np.mean(values))


def run(field):
    grid = Grid(Parameters(destination=DESTINATION, radius_m=RADIUS, spacing_m=1000))
    coarse = Grid(grid.params.model_copy(update={"spacing_m": 2000}))
    dense = Grid(grid.params.model_copy(update={"spacing_m": 250}))
    _, dense_values, dense_count = regular(dense, field)
    dense_bands = build_geometry(dense, dense_values)[1]
    dense_batches = math.ceil((dense_count - 1) / BATCH)

    started = perf_counter()
    axis, xy, max_points = regular(grid, field)
    square = build_geometry(grid, xy)[1]
    square_ms = (perf_counter() - started) * 1000
    max_batches = math.ceil((max_points - 1) / BATCH)

    started = perf_counter()
    sampler, facets, adaptive_points, adaptive_batches = adaptive(coarse, field, max_points, max_batches)
    hex_bands = build_adaptive_geometry(sampler, facets)[1]
    adaptive_ms = (perf_counter() - started) * 1000

    x, y = np.meshgrid(np.arange(-RADIUS + 113, RADIUS, 197), np.arange(-RADIUS + 87, RADIUS, 193))
    inside = x*x + y*y < RADIUS**2
    holdouts = np.column_stack((x[inside], y[inside]))
    truth = field(*holdouts.T)
    square_values, hex_values = square_predictions(axis, xy, holdouts), facet_predictions(facets, holdouts)
    known = np.isfinite(truth)
    comparable = known & np.isfinite(square_values) & np.isfinite(hex_values)

    def metrics(predict, bands, count, batches, elapsed):
        delta_minutes = np.abs(predict[comparable] - truth[comparable]) / 60
        return {
            "mae_min": round(float(np.mean(delta_minutes)), 2),
            "p90_min": round(float(np.percentile(delta_minutes, 90)), 2),
            "mean_iou": round(iou(bands, dense_bands), 3),
            "holdout_coverage": round(float(np.isfinite(predict[known]).mean()), 3),
            "samples": count, "od": count - 1, "batches": batches, "local_ms": round(elapsed),
        }

    return {
        "common_holdouts": int(comparable.sum()), "dense_samples": dense_count, "dense_batches": dense_batches,
        "uniform": metrics(square_values, square, max_points, max_batches, square_ms),
        "adaptive_hex": metrics(hex_values, hex_bands, adaptive_points, adaptive_batches, adaptive_ms),
    }


if __name__ == "__main__":
    for scene in (anisotropic, corridor, barriers):
        print(json.dumps({scene.__name__: run(scene)}))
