import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.providers.amap import AmapProvider
from app.providers.amap_config import AmapRoutingConfig
from app.providers.base import Point
from app.query_store import QueryStore, ScheduleInput
from app.scheduler import dispatch_due
from app.schemas import Parameters
from app.tasks import Jobs

DESTINATION = {"lng": 121.4737, "lat": 31.2304}
API = "/api/v1/isochrones"
PARAMS = Parameters(destination=DESTINATION, radius_m=1000, min_spacing_m=1000)


def success(request):
    origins = request.url.params["origins"].split("|")
    return httpx.Response(200, json={"status": "1", "results": [
        {"origin_id": index, "dest_id": 1, "duration": 600, "distance": 500}
        for index in range(1, len(origins) + 1)
    ]})


@pytest.mark.parametrize("settings", [
    {"batch_size": 0}, {"batch_size": 101}, {"qps": 0}, {"max_concurrency": 0},
])
def test_invalid_amap_configuration(settings):
    with pytest.raises(ValidationError):
        AmapRoutingConfig(**settings)


def test_configured_batch_size_applies_to_estimate_api_schedule_and_request(monkeypatch):
    monkeypatch.setenv("AMAP_WEB_SERVICE_KEY", "offline-batch-limit")
    monkeypatch.setenv("AMAP_BATCH_SIZE", "100")
    monkeypatch.setenv("AMAP_QPS", "3")
    monkeypatch.setenv("AMAP_MAX_CONCURRENCY", "2")
    monkeypatch.delenv("TISO_SETTINGS_FILE", raising=False)
    monkeypatch.delenv("TISO_RESULTS_FILE", raising=False)
    calls = []

    async def upstream(request_url, *, params):
        request = httpx.Request("GET", request_url, params=params)
        calls.append(len(request.url.params["origins"].split("|")))
        return success(request)

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.amap_client, "get", upstream)
        params = PARAMS.model_copy(update={"radius_m": 4000, "max_requests": 1}).model_dump(mode="json")
        budget = client.post(f"{API}/estimate", json=params).json()
        assert budget["fits_budget"] and budget["route_points"] == 90 and budget["batches"] == 1
        created = client.post(f"{API}/jobs", json=params)
        assert created.status_code == 202
        job_path = f"{API}/jobs/{created.json()['job_id']}"
        assert '"status": "completed"' in client.get(f"{job_path}/events").text
        assert calls == [90]
        assert client.post(f"{API}/schedules", json={"params": params, "name": "早高峰", "time": "08:00"}).status_code == 201

    async def validate_overflow():
        def unexpected_request(request):
            pytest.fail("Batch validation must run before HTTP")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
            provider = AmapProvider("offline-batch-limit", client)
            with pytest.raises(ValueError, match="1 to 100"):
                await provider.travel_time_matrix(
                    [Point(str(i), 121.4, 31.2) for i in range(101)], Point("target", 121.5, 31.2), "driving",
                )

    asyncio.run(validate_overflow())


def test_manual_and_scheduled_jobs_share_key_rate_and_in_flight_limit(monkeypatch):
    monkeypatch.delenv("TISO_RESULTS_FILE", raising=False)
    config = AmapRoutingConfig(batch_size=6, qps=20, max_concurrency=2)
    starts = []
    active = peak = 0

    async def upstream(request):
        nonlocal active, peak
        starts.append(asyncio.get_running_loop().time())
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.13)
            return success(request)
        finally:
            active -= 1

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            manual = Jobs(AmapProvider("same-key", client, config), routing_config=config)
            store = QueryStore()
            try:
                scheduled = Jobs(AmapProvider("same-key", client, config), store, routing_config=config)
                due = datetime(2026, 11, 27, tzinfo=UTC)
                schedule = store.create_schedule(
                    ScheduleInput(name="早高峰", time="08:00", params=PARAMS), due - timedelta(minutes=1),
                )
                first = manual.create(PARAMS, manual.estimate(PARAMS)["total_points"])
                dispatch_due(store, scheduled, due)
                second = next(iter(scheduled.items.values()))
                await asyncio.gather(first.task, second.task)
                assert store.history(10, 0)[0]["schedule_id"] == schedule["id"]
                assert store.schedules()[0]["last_status"] == "completed"
                assert all(job.requests_sent == job.provider_batches == 3 for job in (first, second))
            finally:
                store.close()

    asyncio.run(run())
    assert len(starts) == 6 and peak == 2 and active == 0
    assert all(end - start >= 0.049 for start, end in zip(starts, starts[1:]))


@pytest.mark.parametrize("status", [429, 200])
def test_rate_limit_cools_down_other_batches_and_the_retry(status):
    starts = []
    charged = []
    config = AmapRoutingConfig(qps=20, max_concurrency=2)

    async def run():
        throttled = asyncio.Event()

        async def upstream(request):
            starts.append(asyncio.get_running_loop().time())
            if len(starts) == 1:
                throttled.set()
                if status == 429:
                    return httpx.Response(429, headers={"Retry-After": "2"})
                return httpx.Response(200, json={"status": "0", "infocode": "10020"})
            return success(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            first = AmapProvider("cooldown-key", client, config)
            second = AmapProvider("cooldown-key", client, config)
            target = Point("center", 121.4737, 31.2304)

            async def route(provider, label):
                return await provider.travel_time_matrix(
                    [Point(label, 121.45, 31.23)], target, "driving", before_request=lambda: charged.append(label),
                )

            first_task = asyncio.create_task(route(first, "retry"))
            await throttled.wait()
            await asyncio.gather(first_task, route(second, "other"))

    asyncio.run(run())
    assert len(starts) == 3 and sorted(charged) == ["other", "retry", "retry"]
    assert starts[1] - starts[0] >= (1.99 if status == 429 else 0.99)
    assert starts[2] - starts[1] >= 0.049


@pytest.mark.parametrize("rejection", [
    httpx.Response(429, headers={"Retry-After": "60"}),
    httpx.Response(200, json={"status": "0", "infocode": "10004"}),
    httpx.Response(200, json={"status": "0", "infocode": "10044"}),
])
def test_long_rate_limit_and_daily_quota_reject_later_calls_without_charging(rejection):
    charged = []
    config = AmapRoutingConfig(qps=20, max_concurrency=2)

    def upstream(request):
        return rejection if request.url.params["key"] == "limited-key" else success(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            async def route(key, label):
                # A replacement provider shares the original key's throttle state.
                provider = AmapProvider(key, client, config)
                return await provider.travel_time_matrix(
                    [Point(label, 121.45, 31.23)], Point("center", 121.47, 31.23), "driving",
                    before_request=lambda: charged.append(label),
                )

            with pytest.raises(ValueError):
                await route("limited-key", "first")
            with pytest.raises(ValueError):
                await route("limited-key", "later")
            assert (await route("other-key", "independent"))[0].status == "ok"

    asyncio.run(asyncio.wait_for(run(), 2))
    assert charged == ["first", "independent"]


def test_parallel_budget_exhaustion_preserves_successful_samples_and_progress():
    config = AmapRoutingConfig(batch_size=6, qps=1000, max_concurrency=3)
    params = PARAMS.model_copy(update={"radius_m": None, "max_requests": 3})
    calls = []
    snapshots = []

    async def upstream(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        await asyncio.sleep(0.02)
        return success(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            provider = AmapProvider("budget-key", client, config)
            jobs = Jobs(provider, routing_config=config)
            job = jobs.create(params, jobs.estimate(params)["total_points"])
            while not job.task.done():
                changed = job.changed
                snapshots.append(job.snapshot())
                await asyncio.wait_for(changed.wait(), 3)
            snapshots.append(job.snapshot())
            await job.task
            assert job.status == "partial"
            assert job.result["sample_count"] == 13 and job.result["route_points"] == 18
            assert job.result["request_count"] == job.provider_batches == len(calls) == 3
            assert any("已达外部请求预算" in warning for warning in job.result["warnings"])
            assert len({sample["id"] for sample in job.samples}) == job.completed_points == 13

    asyncio.run(run())
    for key in ("sequence", "requests_sent", "completed_points"):
        counters = [snapshot[key] for snapshot in snapshots]
        assert counters == sorted(counters)


def test_cancel_drains_http_and_queued_batches_and_releases_shared_capacity():
    config = AmapRoutingConfig(batch_size=6, qps=1000, max_concurrency=2)
    calls = cancelled = 0

    async def run():
        nonlocal calls, cancelled
        request_started = asyncio.Event()

        async def upstream(request):
            nonlocal calls, cancelled
            calls += 1
            if calls == 2:
                request_started.set()
            if calls <= 2:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled += 1
                    raise
            return success(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            provider = AmapProvider("cancel-key", client, config)
            jobs = Jobs(provider, routing_config=config)
            job = jobs.create(PARAMS, jobs.estimate(PARAMS)["total_points"])
            await asyncio.wait_for(request_started.wait(), 2)
            job.cancel()
            terminal = job.snapshot()
            with pytest.raises(asyncio.CancelledError):
                await job.task
            assert job.snapshot() == terminal
            assert job.status == "cancelled" and job.completed_points == 1 and job.result is None
            assert cancelled == calls == job.requests_sent == 2
            next_job = jobs.create(PARAMS, jobs.estimate(PARAMS)["total_points"])
            await asyncio.wait_for(next_job.task, 2)
            assert next_job.status == "completed" and calls == 5

    asyncio.run(run())
