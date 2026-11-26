import asyncio
import json
import math
import stat
from datetime import UTC, datetime

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point as GeometryPoint, box, shape
from shapely.ops import unary_union

from app.core.field import Grid, build_geometry
from app.core.hex_sampling import meters, ring_radius
from app.core.mainland import inside_mainland, mainland_boundary
from app.main import app
from app.providers.amap import AmapProvider
from app.providers.base import Point, TravelResult
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
def amap_client(monkeypatch):
    monkeypatch.setenv("AMAP_WEB_SERVICE_KEY", "test-routing-key")
    monkeypatch.delenv("TISO_SETTINGS_FILE", raising=False)

    async def upstream(url, *, params):
        lng, lat = map(float, params["destination"].split(","))
        rows = []
        for index, origin in enumerate(params["origins"].split("|"), 1):
            if index == 1:
                continue
            x, y = map(float, origin.split(","))
            distance = math.hypot((x - lng) * 95_000, (y - lat) * 111_000)
            rows.append({"origin_id": index, "dest_id": 1, "duration": distance * 1.5, "distance": distance})
        return httpx.Response(200, json={"status": "1", "results": rows})

    with TestClient(app) as test_client:
        monkeypatch.setattr(app.state.amap_client, "get", upstream)
        yield test_client


def test_local_amap_settings_persist_and_proxy_keeps_credentials_private(monkeypatch, tmp_path):
    saved = tmp_path / "amap.json"
    origin = "http://127.0.0.1:8080"
    url = "/api/v1/settings/amap"
    keys = {"js_key": "public-js-fixture", "js_security_code": "private-js-fixture", "web_service_key": "private-web-fixture"}
    monkeypatch.delenv("AMAP_WEB_SERVICE_KEY", raising=False)
    monkeypatch.setenv("TISO_SETTINGS_FILE", str(saved))

    with TestClient(app, base_url=origin) as client:
        assert client.post(url, json=keys, headers={"Origin": "https://example.org"}).status_code == 403
        assert client.post(url, json={"js_key": keys["js_key"]}, headers={"Origin": origin}).status_code == 422
        assert not saved.exists()
        response = client.post(url, json=keys, headers={"Origin": origin})
        assert response.status_code == 200
        public = {"js_key": keys["js_key"], "routing_configured": True, "web_service_saved": True}
        assert response.json() == public
        assert client.get("/api/v1/capabilities").json()["providers"] == ["amap"]
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600

    with TestClient(app, base_url=origin) as client:
        assert client.get(url).json() == public
        reveal = url + "/reveal"
        selection = {"field": "js_security_code"}
        assert client.get(reveal).status_code == 405
        assert client.post(reveal, json=selection).status_code == 403
        assert client.post(reveal, json=selection, headers={"Origin": "https://example.org"}).status_code == 403
        assert client.post(reveal, json=selection, headers={"Origin": origin, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        for field in ("js_security_code", "web_service_key"):
            response = client.post(reveal, json={"field": field}, headers={"Origin": origin})
            assert response.json() == {"value": keys[field]}
            assert response.headers["cache-control"] == "no-store"
        assert client.post(url, json={"js_key": keys["js_key"]}, headers={"Origin": origin}).json() == public
        assert json.loads(saved.read_bytes()) == keys

        async def upstream(request_url, *, params, headers, timeout):
            assert request_url == "https://restapi.amap.com/v3/place/text"
            assert dict(params)["jscode"] == keys["js_security_code"]
            assert keys["web_service_key"] not in str(params)
            callback = dict(params).get("callback")
            if callback:
                return httpx.Response(200, content=f'{callback}({{"status":"1","pois":[{{"name":"人民广场"}}]}})',
                                      headers={"content-type": "application/json;charset=UTF-8"})
            return httpx.Response(200, json={"status": "1", "echo": keys["js_security_code"]})

        monkeypatch.setattr(app.state.amap_client, "get", upstream)
        proxy = "/_AMapService/v3/place/text"
        assert client.get(proxy, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        headers = {"Sec-Fetch-Site": "same-origin"}
        assert client.get(proxy, params={"key": "wrong"}, headers=headers).status_code == 403
        response = client.get(proxy, params={"key": keys["js_key"], "jscode": "override"}, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"status": "1", "echo": "[redacted]"}

        jsonp_headers = {**headers, "Sec-Fetch-Dest": "script"}
        response = client.get(proxy, params={"callback": "amapSearchCallback", "key": keys["js_key"]},
                              headers=jsonp_headers)
        assert response.headers["content-type"] == "application/javascript; charset=utf-8"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert json.loads(response.text.removeprefix("amapSearchCallback(").removesuffix(");"))["pois"] == [
            {"name": "人民广场"},
        ]
        response = client.get(proxy, params={"callback": "amapSearchCallback);alert(1)", "key": keys["js_key"]},
                              headers=jsonp_headers)
        assert response.headers["content-type"].startswith("application/json")

        response = client.post(url, json={key: "" for key in keys}, headers={"Origin": origin})
        assert response.json() == {"js_key": "", "routing_configured": False, "web_service_saved": False}
        assert client.get("/api/v1/capabilities").json()["providers"] == []
        response = client.post(BASE + "/jobs", json={"destination": DESTINATION})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "provider_unavailable"
        legacy_request = client.post(BASE + "/jobs", json={"destination": DESTINATION, "provider": "mock"})
        assert legacy_request.json()["error"]["code"] == "invalid_parameters"


def test_amap_job_from_estimate_through_sse_to_result(amap_client):
    client = amap_client
    params = {"destination": DESTINATION, "radius_m": 1000, "max_requests": 1}
    estimate = client.post(BASE + "/estimate", json=params).json()
    assert estimate["fits_budget"] and estimate["estimated_requests"] == 1
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
    assert result["provider"] == "amap" and result["request_count"] == 1
    assert result["sampling"] == "adaptive" and result["grid"] == "hexagonal"
    assert result["observed_from"] and result["observed_to"]
    assert result["contours"]["features"] and result["bands"]["features"]
    center = next(sample for sample in result["samples"] if sample["id"] == "0:0")
    assert center["duration_s"] == 0
    assert center["coordinates"] == list(DESTINATION.values())
    assert all(sample["duration_s"] is None for sample in result["samples"]
               if sample["status"] != "ok")


def test_budget_rejection_and_idempotent_cancellation(amap_client):
    client = amap_client
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


@pytest.mark.parametrize("case, max_requests, truncated", [
    ("corridor", 150, False), ("corridor", 2, True), ("missing", 150, True), ("pocket", 150, False),
])
def test_expanding_hex_job_and_sample_cursor(amap_client, monkeypatch, case, max_requests, truncated):
    monkeypatch.setattr("app.tasks.inside_mainland", lambda _lng, _lat: True)
    monkeypatch.setattr("app.tasks.local_mainland", lambda _grid: box(-100_000, -100_000, 100_000, 100_000))
    probes = []
    batches = []
    observed = datetime(2026, 11, 25, tzinfo=UTC)

    async def route(origins, destination, mode, *, before_request):
        assert destination.id == "0:0"
        before_request()
        batches.append(origins)
        expanding = app.state.jobs.items[next(reversed(app.state.jobs.items))].stage == "expanding"
        results = []
        for point in origins:
            r, q = map(int, point.id.split(":"))
            if expanding:
                probes.append((q, r))
            x, y = meters((q, r))
            duration = math.hypot(x * 0.055, y * 0.7)
            if case == "pocket":
                duration = min(math.hypot(x, y) * 0.63, 600 + math.hypot(x - 10_000, y) * 0.4)
            if case == "missing" and y == 0 and x > 0:
                duration = None
            results.append(TravelResult(point.id, duration, None, "ok" if duration is not None else "error",
                                        None, observed))
        return results

    monkeypatch.setattr(app.state.jobs.amap, "travel_time_matrix", route)
    params = {"destination": DESTINATION, "max_duration_min": 15, "min_spacing_m": 500, "max_requests": max_requests}
    created = amap_client.post(BASE + "/jobs", json=params)
    assert created.status_code == 202
    path = BASE + "/jobs/" + created.json()["job_id"]
    amap_client.get(path + "/events")
    result = amap_client.get(path + "/result").json()
    assert result["boundary_truncated"] is truncated
    assert result["request_count"] <= max_requests
    assert [ring_radius(node) for node in probes[:18]] == [4] * 6 + [8] * 12
    assert all(q % 8 == r % 8 == 0 for q, r in probes[18:])
    grid = Grid(Parameters(**params))
    if case == "corridor" and not truncated:
        assert result["minimum_spacing_m"] == 500
        east = max(p[0] for feature in result["contours"]["features"]
                   for segment in feature["geometry"]["coordinates"] for p in segment)
        assert east > grid.coordinates(15_000, 0)[0]
        # The previous full-ring search needed 23 rings before two consecutive safe rings.
        old_seed_requests = sum(math.ceil(6 * index / 20) for index in range(1, 24))
        assert result["request_count"] < old_seed_requests
        assert result["route_points"] < sum(6 * index for index in range(1, 24))
        band = unary_union([shape(feature["geometry"]) for feature in result["bands"]["features"]])
        lng, lat = grid.coordinates(0, 0)
        east_unit, north_unit = grid.coordinates(1, 1)
        expected_area = math.pi * (900 / 0.055) * (900 / 0.7) * (east_unit - lng) * (north_unit - lat)
        assert band.area == pytest.approx(expected_area, rel=0.12)
    elif case == "missing":
        assert result["radius_m"] > 28_000
        assert result["error_count"] > 0
    elif case == "pocket":
        # A far scout sees the pocket before a nearer, skipped location is backfilled.
        assert probes.index((40, 0)) < probes.index((32, 0))
        # The inner backfill shares a request with the next independently scheduled outer scouts.
        def radii(batch):
            return {ring_radius(tuple(map(int, point.id.split(":")))) for point in batch}

        assert any({32, 48} <= radii(batch) for batch in batches)
        band = unary_union([shape(feature["geometry"]) for feature in result["bands"]["features"]])
        assert band.covers(GeometryPoint(*grid.coordinates(10_000, 0)))
        assert not band.covers(GeometryPoint(*grid.coordinates(6_000, 0)))
        center_x, center_y = grid.coordinates(10_000, 0)
        east, north = grid.coordinates(10_001, 1)
        pocket_radii = [
            math.hypot((x - center_x) / (east - center_x), (y - center_y) / (north - center_y))
            for feature in result["contours"]["features"]
            for line in feature["geometry"]["coordinates"] for x, y in line
            if x > grid.coordinates(6000, 0)[0]
        ]
        assert pocket_radii and max(abs(radius - 750) for radius in pocket_radii) < 60
    taken = [point.id for batch in batches for point in batch]
    assert len(taken) == len(set(taken))
    assert all(all(int(axis) % 2 == 0 for axis in node.split(":")) for node in taken)
    collected = []
    for cursor in range(0, len(taken) + 1, 256):
        page = amap_client.get(path + "/samples", params={"after": cursor}).json()
        collected.extend(item["id"] for item in page["samples"])
        assert page["next"] == len(collected)
    assert collected == ["0:0", *taken]
    assert amap_client.get(path + "/samples", params={"after": len(collected)}).json() == {
        "samples": [], "next": len(collected),
    }


def test_refinement_preview_survives_retry_budget_and_keeps_coarse_result(amap_client, monkeypatch):
    routing_calls = []
    coarse_response = app.state.amap_client.get

    async def upstream(url, *, params):
        routing_calls.append(params["origins"])
        if len(routing_calls) > 1:
            return httpx.Response(429)
        return await coarse_response(url, params=params)

    monkeypatch.setattr(app.state.amap_client, "get", upstream)
    params = {"destination": DESTINATION, "radius_m": 1000, "max_requests": 2}
    created = amap_client.post(BASE + "/jobs", json=params)
    assert created.status_code == 202
    path = BASE + "/jobs/" + created.json()["job_id"]
    events = amap_client.get(path + "/events").text
    assert '"preview_ready": true' in events
    assert '"status": "partial"' in events
    result = amap_client.get(path + "/result").json()
    assert not result["preview"] and result["request_count"] == len(routing_calls) == 2
    assert result["sample_count"] == 19
    assert result["bands"]["features"]
    assert any("加密提前结束" in warning for warning in result["warnings"])
    assert set(routing_calls[0].split("|")).isdisjoint(routing_calls[1].split("|"))


def test_outer_routing_error_reports_reason_and_keeps_inner_samples(amap_client, monkeypatch):
    batches = []
    successful_response = app.state.amap_client.get

    async def upstream(url, *, params):
        batches.append(params["origins"].split("|"))
        if len(batches) == 2:
            return httpx.Response(200, json={"status": "0", "infocode": "10044"})
        return await successful_response(url, params=params)

    monkeypatch.setattr(app.state.amap_client, "get", upstream)
    created = amap_client.post(BASE + "/jobs", json={
        "destination": DESTINATION, "max_duration_min": 30, "min_spacing_m": 1000,
    })
    path = BASE + "/jobs/" + created.json()["job_id"]
    assert '"status": "partial"' in amap_client.get(path + "/events").text
    result = amap_client.get(path + "/result").json()
    assert [len(batch) for batch in batches] == [18, 12]
    assert result["sample_count"] == 19 and result["request_count"] == 2
    assert result["radius_m"] == 2000 and result["boundary_truncated"]
    assert "向外采样提前结束：Amap business error (infocode 10044)" in result["warnings"]


@pytest.mark.parametrize("max_requests", [2, 150])
def test_outer_routing_filters_coast_before_requests_and_clips_bands(amap_client, monkeypatch, max_requests):
    lng, lat = DESTINATION.values()
    grid = Grid(Parameters(destination=DESTINATION))
    coast, _ = grid.coordinates(2500, 0)
    batches = []
    monkeypatch.setattr("app.tasks.inside_mainland", lambda x, _y: x < coast)
    monkeypatch.setattr("app.tasks.local_mainland", lambda _grid: box(-100_000, -100_000, 2500, 100_000))

    async def coastal_distance(url, *, params):
        origins = [tuple(map(float, point.split(","))) for point in params["origins"].split("|")]
        assert tuple(map(float, params["destination"].split(","))) == (lng, lat)
        batches.append(origins)
        app.state.jobs.amap.next_request_at = 0.0
        assert all(x < coast for x, _ in origins)
        return httpx.Response(200, json={"status": "1", "results": [
            {"origin_id": i, "dest_id": 1, "duration": distance * 0.35, "distance": distance}
            for i, (x, y) in enumerate(origins, 1)
            for distance in [math.hypot((x - lng) * 95_000, (y - lat) * 111_000)]
        ]})

    monkeypatch.setattr(app.state.amap_client, "get", coastal_distance)
    created = amap_client.post(BASE + "/jobs", json={
        "destination": DESTINATION, "max_duration_min": 30,
        "min_spacing_m": 1000, "max_requests": max_requests,
    })
    path = BASE + "/jobs/" + created.json()["job_id"]
    events = amap_client.get(path + "/events").text
    result = amap_client.get(path + "/result").json()
    assert result["request_count"] == len(batches) <= max_requests
    assert result["outside_scope_count"] > 0
    assert result["unreachable_count"] == 0
    assert all(item["duration_s"] is None and item["coordinates"][0] >= coast
               for item in result["samples"] if item["status"] == "outside_scope")
    bands = unary_union([shape(feature["geometry"]) for feature in result["bands"]["features"]])
    assert bands.bounds[2] <= coast + 1e-8
    if max_requests == 2:
        assert '"status": "partial"' in events
        assert len(batches) == max_requests
        assert any("已达外部请求预算" in warning for warning in result["warnings"])
        return
    assert '"status": "completed"' in events
    assert not result["boundary_truncated"]
    assert not any("向外采样提前结束" in warning for warning in result["warnings"])
    assert any(item["status"] == "ok" and item["coordinates"][0] < lng - 6000 / 95_000
               for item in result["samples"])


def test_destination_outside_mainland_is_rejected_before_routing(amap_client, monkeypatch):
    async def unexpected_request(*args, **kwargs):
        pytest.fail("outside destination triggered an external request")

    monkeypatch.setattr(app.state.amap_client, "get", unexpected_request)
    params = {"destination": {"lng": 114.174, "lat": 22.282}}
    for endpoint in ("/estimate", "/jobs"):
        response = amap_client.post(BASE + endpoint, json=params)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "destination_outside_scope"


def test_shenzhen_coastal_job_uses_bundled_boundary(amap_client, monkeypatch):
    batches = []

    async def land_routes(url, *, params):
        lng, lat = map(float, params["destination"].split(","))
        points = [tuple(map(float, origin.split(","))) for origin in params["origins"].split("|")]
        assert all(inside_mainland(x, y) for x, y in points)
        batches.append(points)
        app.state.jobs.amap.next_request_at = 0.0
        return httpx.Response(200, json={"status": "1", "results": [
            {"origin_id": i, "dest_id": 1, "duration": distance * 0.4, "distance": distance}
            for i, (x, y) in enumerate(points, 1)
            for distance in [math.hypot((x - lng) * 100_000, (y - lat) * 111_000)]
        ]})

    monkeypatch.setattr(app.state.amap_client, "get", land_routes)
    response = amap_client.post(BASE + "/jobs", json={
        "destination": {"lng": 113.945, "lat": 22.522}, "max_duration_min": 15, "min_spacing_m": 1000,
    })
    assert response.status_code == 202
    path = BASE + "/jobs/" + response.json()["job_id"]
    assert '"status": "completed"' in amap_client.get(path + "/events").text
    result = amap_client.get(path + "/result").json()
    assert result["outside_scope_count"] > 0
    assert result["route_points"] == sum(map(len, batches))
    assert result["request_count"] == len(batches)
    boundary = mainland_boundary().buffer(0.0000002)
    for key in ("bands", "contours"):
        assert result[key]["features"]
        assert all(shape(feature["geometry"]).difference(boundary).is_empty for feature in result[key]["features"])


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
