import asyncio
from datetime import UTC, datetime, timedelta
from math import hypot

from fastapi.testclient import TestClient

from app.main import app
from app.providers.amap_config import AmapRoutingConfig
from app.providers.base import Capabilities, TravelResult
from app.query_store import QueryStore, ScheduleInput
from app.scheduler import dispatch_due
from app.schemas import Parameters
from app.tasks import Jobs

API = "/api/v1/isochrones"
PARAMS = {"destination": {"lng": 121.4737, "lat": 31.2304}, "radius_m": 1000, "max_requests": 1}


class OfflineRoutes:
    def __init__(self):
        self.calls = 0

    def capabilities(self):
        return Capabilities(frozenset({"driving"}), True, False, False, AmapRoutingConfig().batch_size)

    async def travel_time_matrix(self, origins, destination, mode, *, before_request):
        assert mode == "driving"
        before_request()
        self.calls += 1
        return [
            TravelResult(point.id, hypot(point.lon - destination.lon, point.lat - destination.lat) * 105_000,
                         None, "ok", None, datetime.now(UTC))
            for point in origins
        ]


def test_history_persists_derived_geometry_and_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("TISO_RESULTS_FILE", str(tmp_path / "queries.sqlite3"))
    with TestClient(app) as client:
        provider = app.state.jobs.amap = OfflineRoutes()
        created = client.post(API + "/jobs", json={**PARAMS, "destination_name": "人民广场"})
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        client.get(f"{API}/jobs/{job_id}/events")
        result = client.get(f"{API}/jobs/{job_id}/result").json()
        assert result["samples"] and provider.calls == 1
        assert client.get(API + "/history").json()["items"][0]["id"] == job_id

    with TestClient(app) as client:
        saved = client.get(f"{API}/history/{job_id}").json()
        assert saved["summary"]["params"]["destination_name"] == "人民广场"
        assert saved["result"]["bands"] == result["bands"]
        assert saved["result"]["samples"] == []
        assert client.delete(f"{API}/history/{job_id}").json() == {"deleted": True}
    with TestClient(app) as client:
        assert client.get(API + "/history").json() == {"items": [], "more": False}
        assert client.get(f"{API}/history/{job_id}").status_code == 404


def test_daily_job_uses_existing_worker_once_and_saves_result(tmp_path, monkeypatch):
    monkeypatch.setenv("TISO_RESULTS_FILE", str(tmp_path / "queries.sqlite3"))
    due = datetime(2026, 11, 27, tzinfo=UTC)
    store = QueryStore()
    schedule = store.create_schedule(ScheduleInput(name="早高峰", time="08:00", params=Parameters(**PARAMS)),
                                     due - timedelta(minutes=1))
    store.close()
    store = QueryStore()
    provider = OfflineRoutes()
    jobs = Jobs(provider, store)

    async def run():
        dispatch_due(store, jobs, due - timedelta(seconds=1))
        assert jobs.active is None
        dispatch_due(store, jobs, due)
        active = jobs.active
        assert active is not None
        dispatch_due(store, jobs, due)
        assert jobs.active is active
        await active
        dispatch_due(store, jobs, due + timedelta(seconds=30))

    asyncio.run(run())
    saved = store.history(20, 0)
    assert len(saved) == 1 and saved[0]["schedule_id"] == schedule["id"] and provider.calls == 1
    plan = store.schedules()[0]
    assert plan["last_status"] == "completed"
    assert plan["next_run"] == (due + timedelta(days=1)).isoformat()
    store.close()


def test_schedule_can_be_paused_resumed_and_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("TISO_RESULTS_FILE", str(tmp_path / "queries.sqlite3"))
    with TestClient(app) as client:
        app.state.jobs.amap = OfflineRoutes()
        response = client.post(API + "/schedules", json={"name": "测试", "time": "08:00", "params": PARAMS})
        assert response.status_code == 201
        schedule_id = response.json()["id"]
        path = API + "/schedules/" + schedule_id
        assert client.patch(path, json={"enabled": False}).json()["next_run"] is None
    with TestClient(app) as client:
        assert client.get(API + "/schedules").json()[0]["enabled"] is False
        assert client.patch(path, json={"enabled": True}).json()["next_run"]
        assert client.delete(path).json() == {"deleted": True}
        assert client.get(API + "/schedules").json() == []
