import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .core.field import Grid, build_result
from .providers.amap import AmapProvider
from .providers.base import DRIVING_CAPABILITIES, TravelResult
from .schemas import Parameters

TERMINAL = {"completed", "partial", "failed", "cancelled"}
LOG = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(UTC)


def estimate(grid: Grid) -> dict:
    points = sum(1 for _ in grid.nodes())
    batches = math.ceil((points - 1) / DRIVING_CAPABILITIES.max_origins)
    return {
        "total_points": points, "route_points": points - 1, "batches": batches,
        "estimated_requests": batches,
        "fits_budget": points <= grid.params.max_points and batches <= grid.params.max_requests,
    }


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
            "provider_batches": self.provider_batches, "error": self.error,
        }

    def cancel(self) -> None:
        if self.status not in TERMINAL:
            self.publish("cancelled", "cancelled")
            # An in-progress geometry thread must finish before another job starts.
            if self.task and not self.building_geometry:
                self.task.cancel()

    building_geometry: bool = False


class Jobs:
    def __init__(self, amap: AmapProvider | None) -> None:
        self.amap = amap
        self.items: dict[str, Job] = {}
        self.active: asyncio.Task | None = None

    def prune(self) -> None:
        cutoff = now() - timedelta(minutes=30)
        for job_id, job in list(self.items.items()):
            if job.finished_at and job.finished_at < cutoff and job.task and job.task.done():
                del self.items[job_id]

    def create(self, params: Parameters, points: int) -> Job:
        if len(self.items) >= 8:
            oldest = next(iter(self.items))
            del self.items[oldest]
        job = Job(params, points)
        self.items[job.id] = job
        job.task = self.active = asyncio.create_task(self.run(job))
        return job

    async def run(self, job: Job) -> None:
        started_at = now()
        grid = Grid(job.params)
        job.publish("running", "sampling")
        nodes = list(grid.nodes())
        origins = [point for _, _, point in nodes if point.id != "0:0"]
        destination = next(point for _, _, point in nodes if point.id == "0:0")
        provider = self.amap
        results = {"0:0": TravelResult("0:0", 0, 0, "ok", None, started_at)}
        job.completed_points = 1
        job.publish("running", "routing")

        def before_request() -> None:
            if job.requests_sent >= job.params.max_requests:
                raise ValueError("已达外部请求预算，含重试")
            job.requests_sent += 1
            job.publish("running", "routing")

        try:
            if provider is None:
                raise ValueError("高德 Web 服务未配置")
            batch_size = provider.capabilities().max_origins
            for offset in range(0, len(origins), batch_size):
                if job.provider_batches >= job.params.max_requests:
                    raise ValueError("已达请求预算")
                batch = origins[offset:offset + batch_size]
                job.provider_batches += 1
                batch_results = await provider.travel_time_matrix(
                    batch, destination, "driving", before_request=before_request,
                )
                for item in batch_results:
                    results[item.point_id] = item
                    observed_at = item.observed_at.isoformat()
                    job.observed_from = min(job.observed_from or observed_at, observed_at)
                    job.observed_to = max(job.observed_to or observed_at, observed_at)
                job.completed_points = len(results)
                job.publish("running", "routing")

            if not any(item.status == "ok" for key, item in results.items() if key != "0:0"):
                raise ValueError("未获得有效算路样本")
            job.building_geometry = True
            job.publish("running", "geometry")
            result = await asyncio.to_thread(build_result, grid, nodes, results)
            if job.status == "cancelled":
                return
            warnings = []
            if result["coverage"] < 0.9999:
                warnings.append("未知区域不填色。")
            if result["boundary_truncated"]:
                warnings.append("可达区可能延伸到分析半径外。")
            if result["geometry_repairs"]:
                warnings.append("已修复部分等时面几何。")
            result.update({
                "job_id": job.id, "provider": job.params.provider,
                "coordinate_system": "GCJ-02",
                "geometry_format": "GCJ-02 map geometry (non-RFC 7946)",
                "direction": "origins_to_destination", "mode": "driving",
                "traffic_mode": job.params.traffic_mode, "destination": job.params.destination.model_dump(),
                "radius_m": job.params.radius_m, "spacing_m": job.params.spacing_m,
                "request_count": job.requests_sent, "route_points": len(origins),
                "provider_batches": job.provider_batches, "warnings": warnings,
                "started_at": started_at.isoformat(), "finished_at": now().isoformat(),
                "observed_from": job.observed_from, "observed_to": job.observed_to,
            })
            job.result = result
            status = "partial" if result["error_count"] or result["unreachable_count"] else "completed"
            job.publish(status, "completed")
        except ValueError as error:
            if job.status != "cancelled":
                job.error = {"code": "job_failed", "message": str(error)}
                job.publish("failed", "failed")
        except Exception as error:
            LOG.error("Job %s failed (%s)", job.id, type(error).__name__)
            if job.status != "cancelled":
                job.error = {"code": "job_failed", "message": "计算失败"}
                job.publish("failed", "failed")
        finally:
            job.building_geometry = False
