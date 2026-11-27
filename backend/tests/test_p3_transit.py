import time
from datetime import UTC, datetime
from math import isclose

from fastapi.testclient import TestClient
from shapely.geometry import Point as GeometryPoint, shape

from app.core.field import Grid
from app.core.metro_geometry import metro_geometry
from app.core.metro_network import MetroEdge, MetroNetwork, Platform, reverse_rides
from app.main import app
from app.providers.amap_metro import parse_metro
from app.providers.base import Point, TravelResult
from app.schemas import Parameters

BASE = "/api/v1/isochrones"
SHANGHAI = {"lng": 121.4737, "lat": 31.2304}
BEIJING = {"lng": 116.4074, "lat": 39.9042}


def test_live_probe_parser_uses_metro_and_walking_only():
    def plan(seconds, *types):
        return {"cost": {"duration": seconds}, "distance": "900", "segments": [
            {"bus": {"buslines": [{"type": mode}]}} for mode in types
        ]}

    origin = Point("x", 121.47, 31.23)
    metro = plan(600, "地铁线路")
    metro["segments"].append({"walking": {"distance": "100"}})
    options = [plan(200, "普通公交"), plan(250, "地铁线路", "普通公交"),
               {"segments": [{"railway": {"id": "train"}}]}, {"segments": [{"taxi": {"distance": "900"}}]}]
    assert parse_metro({"status": "1", "route": {"transits": options}}, origin).status == "unreachable"
    result = parse_metro({"status": "1", "route": {"transits": [*options, metro]}}, origin)
    assert (result.status, result.duration_s) == ("ok", 600)


def test_reverse_network_uses_directed_ride_edges_and_transfer_costs():
    points = [
        Platform(id="a", station="A", name="目标站", line="1", lng=121.4737, lat=31.2304),
        Platform(id="b1", station="B", name="换乘站", line="1", lng=121.4500, lat=31.2304),
        Platform(id="b2", station="B", name="换乘站", line="2", lng=121.4500, lat=31.2304),
        Platform(id="c", station="C", name="出发站", line="2", lng=121.4250, lat=31.2304),
        Platform(id="d", station="D", name="反方向站", line="1", lng=121.4990, lat=31.2304),
    ]
    edges = [
        MetroEdge(origin="b1", destination="a", seconds=120, kind="ride"),
        MetroEdge(origin="b2", destination="b1", seconds=180, kind="transfer"),
        MetroEdge(origin="c", destination="b2", seconds=90, kind="ride"),
        MetroEdge(origin="a", destination="d", seconds=90, kind="ride"),
    ]
    boundary = {"type": "Polygon", "coordinates": [[[121, 31], [122, 31], [122, 32], [121, 32], [121, 31]]]}
    network = MetroNetwork(city_id=913067, city_name="上海市", boundary=boundary, platforms=points, edges=edges)
    grid = Grid(Parameters(destination=SHANGHAI))
    times, seeds = reverse_rides(network, grid, 900)
    assert seeds == ["目标站"]
    assert times == {"B": 240, "C": 510}
    assert reverse_rides(network, grid, 500)[0] == {"B": 240}

    shifted = {"lng": 121.4777, "lat": 31.2304}
    slower = Grid(Parameters(destination=shifted, mode="metro", walk_speed_m_s=1.2))
    faster = Grid(Parameters(destination=shifted, mode="metro", walk_speed_m_s=2.4))
    slow_time = reverse_rides(network, slower, 900)[0]["B"]
    fast_time = reverse_rides(network, faster, 900)[0]["B"]
    assert isclose(slow_time - 240, 2 * (fast_time - 240))


def test_metro_geometry_draws_walkable_bands_even_without_train_service():
    grid = Grid(Parameters(destination=SHANGHAI, mode="metro", radius_m=2000,
                            max_duration_min=15, contour_interval_min=5))
    target = Point("0:0", SHANGHAI["lng"], SHANGHAI["lat"])
    results = {target.id: TravelResult(target.id, 0, 0, "ok", None, datetime.now(UTC))}
    geometry = metro_geometry(grid, [target], results)
    bands = geometry["bands"]["features"]
    assert len(bands) == len(geometry["contours"]["features"]) == 3
    first, second = (shape(feature["geometry"]) for feature in bands[:2])
    assert first.covers(GeometryPoint(*grid.coordinates(350, 0)))
    assert not first.covers(GeometryPoint(*grid.coordinates(370, 0)))
    assert second.covers(GeometryPoint(*grid.coordinates(370, 0)))

    faster = Grid(grid.params.model_copy(update={"walk_speed_m_s": 2.4}))
    expanded = metro_geometry(faster, [target], results)["bands"]["features"][0]
    assert shape(expanded["geometry"]).covers(GeometryPoint(*grid.coordinates(700, 0)))


def test_bundled_shanghai_runs_without_key_or_upstream(monkeypatch, tmp_path):
    monkeypatch.setenv("TISO_METRO_DIR", str(tmp_path))
    monkeypatch.delenv("AMAP_WEB_SERVICE_KEY", raising=False)
    monkeypatch.delenv("TISO_SETTINGS_FILE", raising=False)

    async def no_network(*args, **kwargs):
        raise AssertionError("a bundled metro analysis must stay offline")

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.amap_client, "get", no_network)
        params = {"mode": "metro", "traffic_mode": "static", "destination": SHANGHAI,
                  "max_duration_min": 30, "contour_interval_min": 5}
        estimate = client.post(f"{BASE}/estimate", json=params).json()
        assert estimate["estimated_requests"] == 0 and estimate["fits_budget"]
        assert estimate["metro_network"]["city_name"] == "上海市"
        assert estimate["metro_network"]["station_count"] >= 400
        assert client.post("/api/v1/metro/networks", json=SHANGHAI).json()["status"] == "ready"
        created = client.post(f"{BASE}/jobs", json=params)
        assert created.status_code == 202
        path = f'{BASE}/jobs/{created.json()["job_id"]}'
        assert '"status": "completed"' in client.get(path + "/events").text
        result = client.get(path + "/result").json()
    assert result["request_count"] == 0 and result["sampling"] == "metro_stations"
    assert result["success_count"] > 10
    vertices = sum(len(line) for feature in result["contours"]["features"]
                   for line in feature["geometry"]["coordinates"])
    vertices += sum(len(ring) for feature in result["bands"]["features"]
                    for polygon in feature["geometry"]["coordinates"] for ring in polygon)
    assert vertices < 30_000
    assert next(sample for sample in result["samples"] if sample["id"] == "0:0")["duration_s"] == 0
    bands = [shape(feature["geometry"]) for feature in result["bands"]["features"]]
    assert bands and all(band.is_valid for band in bands)
    assert bands[0].covers(GeometryPoint(SHANGHAI["lng"], SHANGHAI["lat"]))
    assert all(band.intersection(other).area < 1e-12
               for index, band in enumerate(bands) for other in bands[index + 1:])


def test_generate_city_from_destination_persists_and_reuses_graph(monkeypatch, tmp_path):
    monkeypatch.setenv("TISO_METRO_DIR", str(tmp_path))
    monkeypatch.setattr("app.metro_store.BUNDLED", tmp_path / "bundled")
    calls = []
    corners = [(116, 39), (117, 39), (117, 40), (116, 40), (116, 39)]
    nodes = [
        {"type": "node", "id": 1, "lon": 116.400, "lat": 39.904, "tags": {"name": "一站"}},
        {"type": "node", "id": 2, "lon": 116.375, "lat": 39.904, "tags": {"name": "二站"}},
    ]
    data = {"elements": [
        {"type": "relation", "id": 912940, "tags": {"name": "北京市"}, "members": [{
            "role": "outer", "geometry": [{"lon": lng, "lat": lat} for lng, lat in corners],
        }]},
        {"type": "relation", "id": 80, "tags": {"route": "subway", "ref": "1"},
         "members": [{"type": "node", "ref": node["id"], "role": "stop"} for node in reversed(nodes)]},
        *nodes,
    ]}

    async def overpass(client, query):
        calls.append(query)
        if query.startswith("is_in"):
            return {"elements": [{"id": 912940, "tags": {"admin_level": "4"}}]}
        return data

    monkeypatch.setattr("app.providers.osm_metro.overpass", overpass)
    monkeypatch.setattr("app.metro_store.overpass", overpass)
    params = {"mode": "metro", "traffic_mode": "static", "destination": BEIJING}
    with TestClient(app) as client:
        assert client.post(f"{BASE}/estimate", json=params).json()["metro_network"]["status"] == "missing"
        assert client.post(f"{BASE}/jobs", json=params).status_code == 409
        assert client.post("/api/v1/metro/networks", json=BEIJING).json()["status"] == "building"
        deadline = time.monotonic() + 5
        while True:
            status = client.post(f"{BASE}/estimate", json=params).json()["metro_network"]
            if status["status"] != "building" or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert status["status"] == "ready", status
        assert status["station_count"] == 2
        assert client.post("/api/v1/metro/networks", json=BEIJING).json()["status"] == "ready"

    assert (tmp_path / "912940.json.gz").is_file()
    assert len(calls) == 2
    with TestClient(app) as client:
        assert client.post(f"{BASE}/estimate", json=params).json()["metro_network"]["city_name"] == "北京市"
    assert len(calls) == 2
