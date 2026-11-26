import math

from .base import Point

ENDPOINT = "https://restapi.amap.com/v3/distance"


def numeric_field(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("invalid numeric field")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid numeric field")
    return number


def parse_results(payload: object, origins: list[Point]) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("status")) != "1":
        code = str(payload.get("infocode", "")) if isinstance(payload, dict) else ""
        if code.isascii() and code.isdecimal() and len(code) == 5:
            raise ValueError(f"Amap business error (infocode {code})")
        raise ValueError("Amap response lacked a valid success status")
    if not isinstance(payload.get("results"), list):
        raise ValueError("Amap response lacked a results array")
    by_index = {}
    for item in payload["results"]:
        if not isinstance(item, dict):
            raise ValueError("Malformed Amap result")
        origin_id = str(item.get("origin_id"))
        if origin_id not in {str(index) for index in range(1, len(origins) + 1)}:
            raise ValueError("Invalid origin_id in Amap result")
        index = int(origin_id)
        if index in by_index:
            raise ValueError("Duplicate origin_id in Amap result")
        code = str(item.get("code"))
        if code in {"1", "2", "3"}:
            by_index[index] = {"status": "unreachable", "provider_error": f"distance_point_error_{code}"}
            continue
        try:
            if str(item.get("dest_id")) != "1" or item.get("code") not in (None, 0, "0"):
                raise ValueError("unexpected destination or item error code")
            duration = numeric_field(item.get("duration"))
            distance = numeric_field(item.get("distance"))
        except ValueError:
            by_index[index] = {"status": "error", "provider_error": "invalid_or_failed_result"}
        else:
            by_index[index] = {"status": "ok", "duration_s": duration, "distance_m": distance}

    return [
        {"point_id": point.id, **by_index.get(index, {
            "status": "error", "provider_error": "missing_result",
        })}
        for index, point in enumerate(origins, start=1)
    ]
