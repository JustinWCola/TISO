import asyncio
import json
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .amap_settings import read_settings, router as amap_settings_router, routing_key
from .core.mainland import inside_mainland
from .providers.amap import AmapProvider
from .schemas import Parameters
from .tasks import TERMINAL, Jobs, estimate

PREFIX = "/api/v1"
JOBS = f"{PREFIX}/isochrones/jobs"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.amap_client = client
        app.state.amap_settings = read_settings()
        key = routing_key(app.state.amap_settings)
        manager = Jobs(AmapProvider(key, client) if key else None)
        app.state.jobs = manager
        try:
            yield
        finally:
            if manager.active and not manager.active.done():
                for job in manager.items.values():
                    if job.task is manager.active:
                        job.cancel()
                with suppress(asyncio.CancelledError):
                    await manager.active


app = FastAPI(title="TISO", lifespan=lifespan)
app.include_router(amap_settings_router)


def fail(status_code: int, code: str, message: str, **details):
    raise HTTPException(status_code, detail={"code": code, "message": message, "details": details})


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, error: StarletteHTTPException):
    detail = error.detail
    if not isinstance(detail, dict):
        detail = {"code": "http_error", "message": str(detail), "details": {}}
    return JSONResponse(status_code=error.status_code, content={"error": detail}, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {
        "code": "invalid_parameters", "message": "请求参数无效",
        "details": [{"field": ".".join(map(str, item["loc"])), "type": item["type"]}
                    for item in error.errors()],
    }})


@app.get(f"{PREFIX}/health")
async def health():
    return {"status": "ok"}


@app.get(f"{PREFIX}/capabilities")
async def capabilities(request: Request):
    providers = ["amap"] if request.app.state.jobs.amap else []
    return {
        "providers": providers, "modes": ["driving"], "traffic_modes": ["current"],
        "coordinate_system": "GCJ-02", "max_points": 2000, "max_requests": 150,
        "sampling": "adaptive", "grid": "hexagonal",
        "supports_departure_time": False, "supports_arrival_time": False,
    }


@app.post(f"{PREFIX}/isochrones/estimate")
async def estimate_job(params: Parameters):
    validate_destination(params)
    return estimate(params)


def validate_destination(params: Parameters) -> None:
    if not inside_mainland(params.destination.lng, params.destination.lat):
        fail(422, "destination_outside_scope", "目的地须位于中国大陆陆地区域")


@app.post(JOBS, status_code=202)
async def create_job(params: Parameters, request: Request):
    manager: Jobs = request.app.state.jobs
    manager.prune()
    if manager.amap is None:
        fail(422, "provider_unavailable", "请在高德设置中配置 Web 服务 Key")
    validate_destination(params)
    budget = estimate(params)
    if not budget["fits_budget"]:
        fail(422, "budget_exceeded", "超出计算预算", **budget)
    if manager.active and not manager.active.done():
        fail(409, "job_busy", "已有任务正在运行")
    return manager.create(params, budget["total_points"]).snapshot()


def get_job(request: Request, job_id: str):
    manager: Jobs = request.app.state.jobs
    manager.prune()
    job = manager.items.get(job_id)
    if job is None:
        fail(404, "job_not_found", "任务不存在或已过期")
    return job


@app.get(f"{JOBS}/{{job_id}}")
async def job_status(job_id: str, request: Request):
    return get_job(request, job_id).snapshot()


@app.post(f"{JOBS}/{{job_id}}/cancel")
async def cancel(job_id: str, request: Request):
    job = get_job(request, job_id)
    job.cancel()
    return job.snapshot()


@app.get(f"{JOBS}/{{job_id}}/result")
async def job_result(job_id: str, request: Request):
    job = get_job(request, job_id)
    if job.result is None:
        fail(409, "result_unavailable", "任务暂无结果", status=job.status)
    return job.result


@app.get(f"{JOBS}/{{job_id}}/preview")
async def job_preview(job_id: str, request: Request):
    job = get_job(request, job_id)
    if job.preview is None:
        fail(409, "preview_unavailable", "任务暂无预览", status=job.status)
    return job.preview


@app.get(f"{JOBS}/{{job_id}}/samples")
async def job_samples(job_id: str, request: Request, after: int = Query(0, ge=0)):
    samples = get_job(request, job_id).samples
    start = min(after, len(samples))
    batch = samples[start:start + 256]
    return {"samples": batch, "next": start + len(batch)}


@app.get(f"{JOBS}/{{job_id}}/events")
async def job_events(job_id: str, request: Request):
    job = get_job(request, job_id)

    async def stream():
        event = "snapshot"
        while True:
            changed = job.changed
            snapshot = job.snapshot()
            yield f'event: {event}\nid: {snapshot["sequence"]}\ndata: {json.dumps(snapshot)}\n\n'
            if snapshot["status"] in TERMINAL:
                return
            try:
                await asyncio.wait_for(changed.wait(), 10)
            except TimeoutError:
                event = "heartbeat"
            else:
                event = {
                    "completed": "completed", "partial": "completed",
                    "failed": "failed", "cancelled": "cancelled",
                }.get(job.status, "progress")

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
