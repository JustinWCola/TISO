import math
import os
import re
from collections import defaultdict
from urllib.parse import quote

import httpx
import numpy as np
from shapely import transform, union_all
from shapely.geometry import LineString, mapping
from shapely.ops import polygonize

from app.core.field import METRES_PER_DEGREE
from app.core.gcj02 import approximate_wgs84, to_gcj02
from app.core.metro_network import MetroEdge, MetroNetwork, Platform

OVERPASS_URLS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
SHANGHAI_ID = 913067
MUNICIPALITIES = {912940, 912999, SHANGHAI_ID, 913069}
TRANSFER_RADIUS_M = 450
TRANSFER_BASE_S = 180
TRANSFER_WALK_M_PER_S = 1.2
SAME_LINE_CHANGE_S = 120
TRAIN_M_PER_S = 11
STOP_OVERHEAD_S = 25


async def overpass(client: httpx.AsyncClient, query: str) -> dict:
    configured = os.getenv("TISO_OVERPASS_URL")
    endpoints = (configured,) if configured else OVERPASS_URLS
    encoded = quote("[out:json][timeout:22];" + query, safe="")
    for endpoint in endpoints:
        try:
            reply = await client.get(f"{endpoint}?data={encoded}",
                                     headers={"User-Agent": "TISO/1.0 metro network generator"}, timeout=27)
            reply.raise_for_status()
            data = reply.json()
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("elements"), list) and not data.get("remark"):
            return data
    raise ValueError("OpenStreetMap 数据服务暂不可用，请稍后重试")


async def find_city(client: httpx.AsyncClient, lng: float, lat: float) -> int:
    x, y = approximate_wgs84(lng, lat)
    data = await overpass(client, f'is_in({y:.6f},{x:.6f})->.here;'
                          'area.here["boundary"="administrative"]["admin_level"~"^(4|6)$"]->.cities;'
                          'rel(pivot.cities);out tags;')
    for row in data["elements"]:
        if row["id"] in MUNICIPALITIES:
            return row["id"]
    city = next((row for row in data["elements"] if row["tags"].get("admin_level") == "6"), None)
    if city is None:
        raise ValueError("OpenStreetMap 未识别到目的地所属城市")
    return int(city["id"])


def city_query(city_id: int) -> str:
    return (f'rel({city_id})->.city;.city map_to_area ->.a;'
            'rel(area.a)["type"="route"]["route"="subway"]->.routes;'
            'node(r.routes)->.stops;.city out geom;.routes out body;.stops out body;')


def metres(first: tuple[float, float], second: tuple[float, float]) -> float:
    lng, lat = first
    x, y = second
    return METRES_PER_DEGREE * math.hypot((x - lng) * math.cos(math.radians((lat + y) / 2)), y - lat)


def gcj_coordinates(coordinates: np.ndarray) -> np.ndarray:
    return np.asarray([to_gcj02(lng, lat) for lng, lat in coordinates]).reshape(-1, 2)


def city_boundary(row: dict) -> dict:
    pieces = {"outer": [], "inner": []}
    for member in row.get("members", []):
        geometry = member.get("geometry", [])
        if member.get("role") in pieces and len(geometry) >= 2:
            pieces[member["role"]].append(LineString([(p["lon"], p["lat"]) for p in geometry]))
    outer = union_all(list(polygonize(pieces["outer"])))
    inner = union_all(list(polygonize(pieces["inner"])))
    boundary = outer.difference(inner).simplify(0.0001, preserve_topology=True)
    return mapping(transform(boundary, gcj_coordinates))


def stop_name(node: dict) -> str:
    tags = node.get("tags", {})
    return (tags.get("name:zh-Hans") or tags.get("name:zh") or tags.get("name") or "").strip()


def build_network(city_id: int, data: dict) -> MetroNetwork:
    rows = data["elements"]
    city = next((row for row in rows if row["type"] == "relation" and row["id"] == city_id), None)
    if city is None:
        raise ValueError("OpenStreetMap 未返回城市边界")
    nodes = {row["id"]: row for row in rows if row["type"] == "node"}
    clusters: dict[str, list[tuple[str, tuple[float, float]]]] = defaultdict(list)
    platforms: dict[str, Platform] = {}
    edges: dict[tuple[str, str], MetroEdge] = {}

    def platform(route_id: int, line: str, node: dict) -> Platform:
        name = stop_name(node)
        longitude, latitude = node["lon"], node["lat"]
        lng, lat = to_gcj02(longitude, latitude)
        key = re.sub(r"\s+", "", name).removesuffix("地铁站").removesuffix("站")
        group = clusters[key]
        station_id = next((ref for ref, coord in group if metres(coord, (lng, lat)) <= TRANSFER_RADIUS_M), None)
        if station_id is None:
            station_id = str(node["id"])
            group.append((station_id, (lng, lat)))
        platform_id = f"{route_id}:{station_id}"
        if platform_id not in platforms:
            platforms[platform_id] = Platform(id=platform_id, station=station_id, name=name,
                                              line=line, lng=round(lng, 6), lat=round(lat, 6))
        return platforms[platform_id]

    routes = [row for row in rows if row["type"] == "relation" and row.get("tags", {}).get("route") == "subway"]
    for route in sorted(routes, key=lambda row: row["id"]):
        tags = route["tags"]
        line = tags.get("ref") or re.split(r"[:：]", tags.get("name", ""), maxsplit=1)[0]
        if not line:
            continue
        members = [member for member in route.get("members", []) if member.get("type") == "node"]
        stops = [member for member in members if member.get("role", "").startswith("stop")]
        if len(stops) < 2:
            stops = [member for member in members if member.get("role", "").startswith("platform")]
        previous = None
        incomplete = False
        segments = []
        for member in stops:
            node = nodes.get(member["ref"])
            if node is None or not stop_name(node):
                previous = None
                incomplete = True
                continue
            current = platform(route["id"], line, node)
            if previous and previous.id != current.id:
                distance = metres((previous.lng, previous.lat), (current.lng, current.lat))
                seconds = max(45, round(distance / TRAIN_M_PER_S + STOP_OVERHEAD_S))
                segments.append((previous.id, current.id, seconds))
            previous = current
        scale = 1.0
        duration = re.fullmatch(r"(\d{1,2}):([0-5]\d)", tags.get("duration", "")) if not incomplete else None
        if duration and segments:
            hours, minutes = map(int, duration.groups())
            ratio = (hours * 3600 + minutes * 60) / sum(seconds for _, _, seconds in segments)
            if 0.6 <= ratio <= 1.8:
                scale = ratio
        for origin, destination, elapsed in segments:
            pair = (origin, destination)
            seconds = max(30, round(elapsed * scale))
            if pair not in edges or seconds < edges[pair].seconds:
                edges[pair] = MetroEdge(origin=origin, destination=destination, seconds=seconds, kind="ride")

    stations: dict[str, list[Platform]] = defaultdict(list)
    linked = {platform_id for edge in edges for platform_id in edge}
    for item in platforms.values():
        if item.id in linked:
            stations[item.station].append(item)
    for group in stations.values():
        for origin in group:
            for destination in group:
                if origin.id == destination.id:
                    continue
                distance = metres((origin.lng, origin.lat), (destination.lng, destination.lat))
                base = SAME_LINE_CHANGE_S if origin.line == destination.line else TRANSFER_BASE_S
                seconds = base + round(distance / TRANSFER_WALK_M_PER_S)
                edges[origin.id, destination.id] = MetroEdge(
                    origin=origin.id, destination=destination.id, seconds=seconds, kind="transfer",
                )
    tags = city.get("tags", {})
    return MetroNetwork(
        city_id=city_id, city_name=tags.get("name:zh") or tags.get("name") or str(city_id),
        boundary=city_boundary(city), data_date=data.get("osm3s", {}).get("timestamp_osm_base"),
        platforms=[item for item in platforms.values() if item.id in linked], edges=list(edges.values()),
    )
