import asyncio
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .core.adaptive_geometry import build_adaptive_result
from .core.field import Grid, sample_record
from .core.hex_sampling import (
    FRONTIER_BUFFER_S, INNER_RINGS, MAX_REFINEMENT_ROUNDS, MIN_SPACING_M, OUTER_STEP_MULTIPLIER,
    HexSampler, Node, meters, ring_nodes, ring_radius, seed_nodes, survey_outer,
)
from .core.mainland import inside_mainland, local_mainland
from .core.metro_network import MetroNetwork
from .metro_job import run_metro
from .providers.amap import AmapProvider
from .providers.base import DRIVING_CAPABILITIES, Point, TravelResult
from .schemas import Parameters
from .query_store import QueryStore

TERMINAL = {"completed", "partial", "failed", "cancelled"}
LOG = logging.getLogger(__name__)
EXPANSION_LIMIT_M = 60_000


def now() -> datetime:
    return datetime.now(UTC)


def routing_grid(params: Parameters) -> Grid:
    expanding = params.radius_m is None
    spacing = (1000 if params.max_duration_min <= 30 else 2000) if expanding else params.spacing_m
    return Grid(params.model_copy(update={
        "radius_m": EXPANSION_LIMIT_M if expanding else params.radius_m,
        "spacing_m": max(spacing, params.min_spacing_m),
    }))


def estimate(params: Parameters) -> dict:
    grid = routing_grid(params)
    nodes = ([(0, 0), *inner_nodes(grid)][:max(7, params.max_points)]
             if params.radius_m is None else list(seed_nodes(grid)))
    points = len(nodes)
    route_points = sum(node != (0, 0) and inside_mainland(*grid.coordinates(*meters(node))) for node in nodes)
    batch_size = DRIVING_CAPABILITIES.max_origins
    batches = math.ceil(route_points / batch_size)
    possible_extra = max(0, params.max_requests - batches) * batch_size
    return {
        "total_points": points, "route_points": route_points, "batches": batches,
        "estimated_requests": batches, "point_limit": min(params.max_points, points + possible_extra),
        "request_limit": params.max_requests,
        "fits_budget": points <= params.max_points and batches <= params.max_requests,
    }


def inner_nodes(grid: Grid) -> list[Node]:
    step = grid.params.spacing_m // MIN_SPACING_M
    return [node for index in range(1, INNER_RINGS + 1) for node in ring_nodes(index, step)]


@dataclass
class Job:
    params: Parameters
    total_points: int
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "queued"
    stage: str = "queued"
    sequence: int = 0
    completed_points: int = 0
    requests_sent: int = 0
    provider_batches: int = 0
    observed_from: str | None = None
    observed_to: str | None = None
    error: dict | None = None
    result: dict | None = None
    preview: dict | None = None
    samples: list[dict] = field(default_factory=list)
    finished_at: datetime | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def publish(self, status: str, stage: str) -> None:
        self.status, self.stage = status, stage
        self.sequence += 1
        if status in TERMINAL:
            self.finished_at = now()
        self.changed.set()
        self.changed = asyncio.Event()

    def snapshot(self) -> dict:
        return {
            "job_id": self.id, "status": self.status, "stage": self.stage,
            "sequence": self.sequence, "total_points": self.total_points,
            "completed_points": self.completed_points, "requests_sent": self.requests_sent,
            "provider_batches": self.provider_batches, "error": self.error, "preview_ready": self.preview is not None,
        }

    def cancel(self) -> None:
        if self.status not in TERMINAL:
            self.preview = None
            self.publish("cancelled", "cancelled")
            # An in-progress geometry thread must finish before another job starts.
            if self.task and not self.building_geometry:
                self.task.cancel()

    building_geometry: bool = False


class Jobs:
    def __init__(self, amap: AmapProvider | None, store: QueryStore | None = None) -> None:
        self.amap = amap
        self.store = store
        self.items: dict[str, Job] = {}
        self.active: asyncio.Task | None = None

    def prune(self) -> None:
        cutoff = now() - timedelta(minutes=30)
        for job_id, job in list(self.items.items()):
            if job.finished_at and job.finished_at < cutoff and job.task and job.task.done():
                del self.items[job_id]

    def create(
        self, params: Parameters, points: int, network: MetroNetwork | None = None,
        *, schedule_id: str | None = None, schedule_name: str | None = None,
    ) -> Job:
        if len(self.items) >= 8:
            oldest = next(iter(self.items))
            del self.items[oldest]
        job = Job(params, points)
        self.items[job.id] = job
        job.task = self.active = asyncio.create_task(self.run_recorded(job, network, schedule_id, schedule_name))
        return job

    async def run_recorded(
        self, job: Job, network: MetroNetwork | None, schedule_id: str | None, schedule_name: str | None,
    ) -> None:
        try:
            await self.run(job, network)
        finally:
            store = self.store
            if store is not None:
                try:
                    if job.result is not None and job.status in {"completed", "partial"}:
                        store.save_result(job.id, job.params, job.result, job.status, schedule_id, schedule_name)
                    if schedule_id:
                        store.finish_schedule(schedule_id, job.status, job.error["message"] if job.error else None)
                except sqlite3.Error:
                    LOG.error("Could not save TISO query history or schedule status")

    async def run(self, job: Job, network: MetroNetwork | None = None) -> None:
        started_at = now()
        grid = routing_grid(job.params)
        expanding = job.params.radius_m is None
        job.publish("running", "sampling")
        requested: set[str] = set()
        results = {"0:0": TravelResult("0:0", 0, 0, "ok", None, started_at)}

        try:
            if network is not None:
                await run_metro(job, network, started_at)
                return
            provider = self.amap
            if provider is None:
                raise ValueError("高德 Web 服务未配置")
            sampler = HexSampler(grid, expanding=expanding)
            destination = sampler.points[(0, 0)]
            if not inside_mainland(destination.lon, destination.lat):
                raise ValueError("目的地须位于中国大陆陆地区域")
            mainland = await asyncio.to_thread(local_mainland, grid)
            batch_size = provider.capabilities().max_origins
            job.completed_points = 1
            job.samples.append(sample_record(destination, results[destination.id]))

            async def route(points: list[Point], stage: str) -> None:
                job.publish("running", stage)
                eligible = []
                for point in points:
                    if inside_mainland(point.lon, point.lat):
                        eligible.append(point)
                        continue
                    item = TravelResult(point.id, None, None, "outside_scope", "outside_mainland", now())
                    results[point.id] = item
                    job.samples.append(sample_record(point, item))
                if len(eligible) != len(points):
                    job.completed_points = len(results)
                    job.publish("running", stage)

                for offset in range(0, len(eligible), batch_size):
                    if job.requests_sent >= job.params.max_requests:
                        raise ValueError("已达外部请求预算，含重试")
                    batch = eligible[offset:offset + batch_size]

                    def before_request() -> None:
                        if job.requests_sent >= job.params.max_requests:
                            raise ValueError("已达外部请求预算，含重试")
                        requested.update(point.id for point in batch)
                        job.requests_sent += 1
                        job.publish("running", stage)

                    job.provider_batches += 1
                    batch_results = await provider.travel_time_matrix(
                        batch, destination, "driving", before_request=before_request,
                    )
                    batch_points = {point.id: point for point in batch}
                    for item in batch_results:
                        results[item.point_id] = item
                        job.samples.append(sample_record(batch_points[item.point_id], item))
                        observed_at = item.observed_at.isoformat()
                        job.observed_from = min(job.observed_from or observed_at, observed_at)
                        job.observed_to = max(job.observed_to or observed_at, observed_at)
                    job.completed_points = len(results)
                    job.publish("running", stage)

            async def make_geometry() -> dict:
                job.building_geometry = True
                try:
                    return await asyncio.to_thread(build_adaptive_result, sampler, results, mainland)
                finally:
                    job.building_geometry = False

            def annotate(result: dict, *, preview: bool, refinement_error: str | None = None) -> dict:
                warnings = []
                if result["coverage"] < 0.9999:
                    warnings.append("未知区域不填色。")
                if result["boundary_truncated"]:
                    warnings.append("外缘尚未确认超出最长耗时，等时圈可能仍向外延伸。" if expanding
                                    else "可达区可能延伸到分析半径外。")
                if refinement_error:
                    warnings.append(f"加密提前结束：{refinement_error}")
                if expansion_error:
                    warnings.append(f"向外采样提前结束：{expansion_error}")
                return {
                    **result, "job_id": job.id, "provider": job.params.provider, "preview": preview,
                    "coordinate_system": "GCJ-02", "geometry_format": "GCJ-02 map geometry (non-RFC 7946)",
                    "sampling": job.params.sampling, "grid": "hexagonal",
                    "direction": "origins_to_destination", "mode": "driving",
                    "traffic_mode": job.params.traffic_mode, "destination": job.params.destination.model_dump(),
                    "radius_m": radius, "spacing_m": grid.params.spacing_m,
                    "min_spacing_m": job.params.min_spacing_m,
                    "request_count": job.requests_sent, "route_points": len(requested),
                    "provider_batches": job.provider_batches, "warnings": warnings,
                    "started_at": started_at.isoformat(), "finished_at": now().isoformat(),
                    "observed_from": job.observed_from, "observed_to": job.observed_to,
                }

            radius = grid.params.radius_m
            expansion_error = None
            if expanding:
                def is_unreachable(node: Node) -> bool:
                    point = sampler.points.get(node)
                    item = results.get(point.id) if point is not None else None
                    return item is not None and item.status in {"unreachable", "outside_scope"}

                async def visit(nodes: list[Node]) -> bool:
                    nonlocal expansion_error
                    pending = [node for node in dict.fromkeys(nodes)
                               if (point := sampler.points.get(node)) is None or point.id not in results]
                    if not pending:
                        return True
                    capacity = job.params.max_points - len(results)
                    points = [sampler.add_point(node) for node in pending[:capacity]]
                    if not points:
                        return False
                    job.total_points = len(results) + len(points)
                    try:
                        await route(points, "expanding")
                    except ValueError as error:
                        expansion_error = str(error)
                        return False
                    return len(points) == len(pending)

                if await visit(inner_nodes(grid)):
                    coarse_step = OUTER_STEP_MULTIPLIER * sampler.seed_step
                    sampler.frontier_closed = await survey_outer(
                        grid.params.radius_m // (coarse_step * MIN_SPACING_M), coarse_step,
                        sampler.levels[-1] + FRONTIER_BUFFER_S, visit, lambda: sampler.measurements(results),
                        is_unreachable=is_unreachable,
                    )
                sampled = sampler.measurements(results)
                radius = max(map(ring_radius, sampled)) * MIN_SPACING_M
                sampler.seed_expanded_mesh(results)
            else:
                await route([point for point in sampler.points.values() if point != destination], "routing")
            if not any(item.status == "ok" for key, item in results.items() if key != destination.id):
                raise ValueError("未获得有效算路样本")

            refinement_error = None
            while sampler.rounds < MAX_REFINEMENT_ROUNDS:
                request_capacity = (job.params.max_requests - job.requests_sent) * batch_size
                point_capacity = job.params.max_points - len(results)
                limit = min(request_capacity, point_capacity)
                if limit <= 0:
                    break
                plan = sampler.plan(results, limit, batch_size=batch_size)
                if not plan.points and not plan.cells:
                    break
                if job.preview is None:
                    job.publish("running", "preview")
                    draft = await make_geometry()
                    if job.status == "cancelled":
                        return
                    job.preview = annotate(draft, preview=True)
                job.total_points = len(results) + len(plan.points)
                job.publish("running", "refining")
                try:
                    await route(plan.points, "refining")
                except ValueError as error:
                    refinement_error = str(error)
                sampler.apply(plan, results)
                if refinement_error:
                    break

            job.total_points = len(results)
            job.publish("running", "geometry")
            result = await make_geometry()
            if job.status == "cancelled":
                return
            job.result = annotate(result, preview=False, refinement_error=refinement_error)
            job.preview = None
            status = ("partial" if expansion_error or refinement_error or result["error_count"] or result["unreachable_count"]
                      or (expanding and result["boundary_truncated"]) else "completed")
            job.publish(status, "completed")
        except ValueError as error:
            if job.status != "cancelled":
                job.preview = None
                job.error = {"code": "job_failed", "message": str(error)}
                job.publish("failed", "failed")
        except Exception as error:
            LOG.error("Job %s failed (%s)", job.id, type(error).__name__)
            if job.status != "cancelled":
                job.preview = None
                job.error = {"code": "job_failed", "message": "计算失败"}
                job.publish("failed", "failed")
