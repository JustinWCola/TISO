import asyncio
import json
import math

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape

from app.core.field import Grid, build_geometry
from app.main import app
from app.providers.amap import AmapProvider
from app.providers.base import Point
from app.schemas import Parameters

BASE = "/api/v1/isochrones"
DESTINATION = {"lng": 121.4737, "lat": 31.2304}


def grid_parameters(**overrides):
    return Parameters(destination=DESTINATION, radius_m=5000, **overrides)


def test_geometry_masks_missing_cells_and_preserves_holes_and_nonoverlap():
    grid = Grid(grid_parameters())
    axis = np.arange(-grid.extent, grid.extent + 1) * grid.params.spacing_m
    durations = np.hypot(axis[None, :], axis[:, None]) * 0.5
    durations[grid.extent + 1, grid.extent + 1] = np.nan

    contours, bands, coverage, truncated, _ = build_geometry(grid, durations)
    shapes = [shape(item["geometry"]) for item in bands["features"]]
    assert len(shapes) == 3
    assert any(p.interiors for band in shapes for p in band.geoms)
    assert all(band.is_valid for band in shapes)
    for index, band in enumerate(shapes):
        assert all(band.intersection(other).area < 1e-14 for other in shapes[index + 1:])
    assert 0 < coverage < 1
    assert truncated
    assert contours["features"][0]["properties"]["duration_min"] == 15
    coordinates = contours["features"][0]["geometry"]["coordinates"]
    lng, lat = grid.coordinates(0, 0)
    one_km_lon, one_km_lat = grid.coordinates(1000, 1000)
    distances = [math.hypot((x - lng) / (one_km_lon - lng),
                            (y - lat) / (one_km_lat - lat))
                 for segment in coordinates for x, y in segment]
    assert distances
    assert max(abs(distance - 1.8) for distance in distances) < 0.15


def test_all_masked_geometry_is_empty():
    grid = Grid(grid_parameters())
    durations = np.full((grid.extent * 2 + 1,) * 2, np.nan)
    contours, bands, coverage, truncated, _ = build_geometry(grid, durations)
    assert contours["features"] == bands["features"] == []
    assert coverage == 0 and not truncated


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AMAP_WEB_SERVICE_KEY", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def test_mock_job_from_estimate_through_sse_to_result(client):
    params = {"destination": DESTINATION, "radius_m": 5000}
    estimate = client.post(BASE + "/estimate", json=params).json()
    assert estimate["fits_budget"] and estimate["estimated_requests"] == 0
    response = client.post(BASE + "/jobs", json=params)
    assert response.status_code == 202
    job_path = BASE + "/jobs/" + response.json()["job_id"]
    events = client.get(job_path + "/events")
    assert events.headers["content-type"].startswith("text/event-stream")
    snapshots = [
        json.loads(line.removeprefix("data: ")) for line in events.text.splitlines()
        if line.startswith("data: ")
    ]
    assert snapshots[-1]["status"] == "partial"
    assert [s["sequence"] for s in snapshots] == sorted({s["sequence"] for s in snapshots})

    result = client.get(job_path + "/result").json()
    assert result["sample_count"] == estimate["total_points"]
    assert result["direction"] == "origins_to_destination"
    assert result["simulated"] and result["request_count"] == 0
    assert result["contours"]["features"] and result["bands"]["features"]
    center = next(sample for sample in result["samples"] if sample["id"] == "0:0")
    assert center["duration_s"] == 0
    assert center["coordinates"] == list(DESTINATION.values())
    assert all(sample["duration_s"] is None for sample in result["samples"]
               if sample["status"] != "ok")


def test_budget_rejection_and_idempotent_cancellation(client):
    params = {"destination": DESTINATION, "max_points": 2}
    assert client.post(BASE + "/estimate", json=params).json()["fits_budget"] is False
    limited = client.post(BASE + "/jobs", json=params)
    assert limited.status_code == 422
    assert limited.json()["error"]["code"] == "budget_exceeded"

    created = client.post(BASE + "/jobs", json={"destination": DESTINATION})
    assert created.status_code == 202
    path = BASE + "/jobs/" + created.json()["job_id"]
    assert client.post(path + "/cancel").json()["status"] == "cancelled"
    assert client.post(path + "/cancel").json()["status"] == "cancelled"
    assert client.get(path + "/result").status_code == 409


def test_amap_request_is_reverse_and_restores_result_order():
    def upstream(request):
        assert request.url.params["origins"] == "121.100000,31.100000|121.200000,31.200000"
        assert request.url.params["destination"] == "121.473700,31.230400"
        assert request.url.params["type"] == "1"
        return httpx.Response(200, json={"status": "1", "results": [
            {"origin_id": "2", "dest_id": "1", "duration": "50", "distance": "550"},
            {"origin_id": "1", "dest_id": "1", "duration": "30", "distance": "330"},
        ]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            provider = AmapProvider("offline-fixture", client)
            return await provider.travel_time_matrix(
                [Point("a", 121.1, 31.1), Point("b", 121.2, 31.2)],
                Point("dst", *DESTINATION.values()), "driving",
            )

    assert [(item.point_id, item.duration_s) for item in asyncio.run(run())] == [
        ("a", 30), ("b", 50),
    ]


def test_amap_retry_checks_budget_before_sending():
    sent = []

    def upstream(request):
        sent.append(request.url.host)
        return httpx.Response(429, headers={"Retry-After": "0"})

    def budget():
        if sent:
            raise ValueError("budget exhausted")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            provider = AmapProvider("offline-fixture", client)
            return await provider.travel_time_matrix(
                [Point("a", 121.1, 31.1)], Point("dst", *DESTINATION.values()),
                "driving", before_request=budget,
            )

    with pytest.raises(ValueError, match="budget exhausted"):
        asyncio.run(run())
    assert sent == ["restapi.amap.com"]
