import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .amap_response import check_success, numeric_field
from .base import Point, TravelResult

AMAP_REST = "https://restapi.amap.com"
METRO_ENDPOINT = AMAP_REST + "/v5/direction/transit/integrated"
BEIJING = ZoneInfo("Asia/Shanghai")
METRO_TYPES = ("地铁", "地铁线路")
POI_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def coordinates(point: Point) -> str:
    return f"{point.lon:.6f},{point.lat:.6f}"


def route_parameters(origin: Point, destination: Point, citycode: str, departure: datetime) -> dict:
    if departure.utcoffset() is None:
        raise ValueError("地铁出发时间须包含时区")
    local = departure.astimezone(BEIJING)
    return {
        "origin": coordinates(origin), "destination": coordinates(destination),
        "originpoi": origin.id, "destinationpoi": destination.id,
        "city1": citycode, "city2": citycode, "strategy": "6", "show_fields": "cost",
        "date": local.strftime("%Y-%m-%d"), "time": local.strftime("%H-%M"),
    }


def parse_metro(payload: object, origin: Point) -> TravelResult:
    payload = check_success(payload)
    observed_at = datetime.now(UTC)
    route = payload.get("route")
    options = route.get("transits") if isinstance(route, dict) else None
    if options == [] or (options is None and str(payload.get("count")) == "0"):
        return TravelResult(origin.id, None, None, "unreachable", "no_metro_route", observed_at)
    try:
        if not isinstance(options, list):
            raise ValueError("missing metro options")
        times = []
        for option in options:
            segments = option["segments"]
            if not isinstance(segments, list) or not segments:
                raise ValueError("missing metro segments")
            if any(segment.get(mode) for segment in segments for mode in ("taxi", "railway")):
                continue
            lines = [line for segment in segments if segment.get("bus")
                     for line in segment["bus"]["buslines"]]
            if not lines or any(line.get("type") not in METRO_TYPES for line in lines):
                continue
            duration = numeric_field(option["cost"]["duration"])
            if duration <= 0:
                raise ValueError("invalid metro duration")
            times.append((duration, numeric_field(option["distance"])))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return TravelResult(origin.id, None, None, "error", "invalid_metro_result", observed_at)
    if not times:
        return TravelResult(origin.id, None, None, "unreachable", "no_metro_route", observed_at)
    duration, distance = min(times)
    return TravelResult(origin.id, duration, distance, "ok", None, observed_at)
