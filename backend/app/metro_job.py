import asyncio
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .core.field import Grid
from .core.metro_geometry import metro_geometry
from .core.metro_network import MetroNetwork, reverse_rides
from .providers.base import Point, TravelResult
from .schemas import Parameters

if TYPE_CHECKING:
    from .tasks import Job


def metro_estimate(params: Parameters, status: dict | None) -> dict:
    stations = (status or {}).get("station_count", 0)
    return {
        "total_points": min(params.max_points, stations + 1), "route_points": 0, "batches": 0,
        "estimated_requests": 0, "point_limit": params.max_points, "request_limit": 0,
        "fits_budget": bool(stations and params.max_points >= 2), "metro_network": status,
    }


def build_result(params: Parameters, network: MetroNetwork, started_at: datetime) -> dict:
    center = Point("0:0", params.destination.lng, params.destination.lat)
    grid = Grid(params)
    limit = params.max_duration_min * 60
    walk_speed = params.walk_speed_m_s
    durations, arrival_stations = reverse_rides(network, grid, limit)
    points = [center]
    samples = {center.id: TravelResult(center.id, 0, 0, "ok", None, started_at)}
    names = {}
    for platform in network.platforms:
        station = platform.station
        if station in durations and station not in samples:
            points.append(Point(station, platform.lng, platform.lat))
            samples[station] = TravelResult(station, durations[station], None, "ok", None, started_at)
            names[station] = platform.name
    if params.radius_m:
        points = [point for point in points if math.hypot(*grid.local_meters(point.lon, point.lat))
                  <= params.radius_m + (limit - samples[point.id].duration_s) * walk_speed]
    limited = len(points) > params.max_points
    if limited:
        points = [center, *sorted(points[1:], key=lambda point: samples[point.id].duration_s)[:params.max_points - 1]]
    extent = params.radius_m or math.ceil(max(
        math.hypot(*grid.local_meters(point.lon, point.lat))
        + max(0, (limit - samples[point.id].duration_s) * walk_speed)
        for point in points
    )) + 100
    grid = Grid(params.model_copy(update={"radius_m": extent}))
    result = metro_geometry(grid, points, samples)
    warnings = [f"站间和换乘耗时为静态估算。站外范围按 {walk_speed:g} 米/秒直线计算。"]
    if not arrival_stations:
        warnings.append("目的地步行范围内没有可用地铁站。")
    if limited:
        warnings.append("已达地铁站点上限。")
    if result["boundary_truncated"]:
        warnings.append("步行可达范围可能延伸到分析半径外。")
    for sample in result["samples"]:
        if sample["id"] in names:
            sample["station_name"] = names[sample["id"]]
    return {
        **result, "radius_m": extent, "arrival_stations": arrival_stations, "warnings": warnings,
        "partial": limited or result["boundary_truncated"],
    }


async def run_metro(job: "Job", network: MetroNetwork, started_at: datetime) -> None:
    params = job.params
    job.publish("running", "metro_routing")
    job.building_geometry = True
    try:
        result = await asyncio.to_thread(build_result, params, network, started_at)
    finally:
        job.building_geometry = False
    if job.status == "cancelled":
        return
    samples = result["samples"]
    job.samples.extend(samples)
    job.total_points = job.completed_points = len(samples)
    partial = result.pop("partial")
    job.result = {
        **result, "job_id": job.id, "provider": "osm", "mode": "metro", "preview": False,
        "traffic_mode": "static", "coordinate_system": "GCJ-02", "metro_network": network.summary(),
        "geometry_format": "GCJ-02 map geometry (non-RFC 7946)", "sampling": "metro_stations",
        "direction": "origins_to_destination", "destination": params.destination.model_dump(),
        "spacing_m": 0, "request_count": 0, "route_points": len(samples) - 1, "provider_batches": 0,
        "walk_speed_m_s": params.walk_speed_m_s, "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(), "observed_from": None, "observed_to": None,
    }
    job.publish("partial" if partial else "completed", "completed")
