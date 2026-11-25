"""Bounded, opt-in smoke test for Amap's candidate distance endpoint."""

import argparse
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener

from app.providers.base import Point

from app.providers.amap_response import ENDPOINT, parse_results
DESTINATION = Point("destination", 121.4737, 31.2304)
ORIGINS = [
    Point("west", 121.4637, 31.2304),
    Point("east", 121.4837, 31.2304),
    Point("north", 121.4737, 31.2404),
]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(key: str, origins: list[Point]) -> list[dict]:
    params = urlencode({
        "key": key,
        "origins": "|".join(f"{point.lon},{point.lat}" for point in origins),
        "destination": f"{DESTINATION.lon},{DESTINATION.lat}",
        "type": "1",
    })
    try:
        with build_opener(NoRedirect()).open(f"{ENDPOINT}?{params}", timeout=10) as response:
            body = response.read(65_537)
    except HTTPError as error:
        raise ValueError(f"Amap HTTP status {error.code}") from None
    except (URLError, OSError, ValueError):
        raise ValueError("Amap connection failed or timed out") from None
    if len(body) > 65_536:
        raise ValueError("Amap response exceeded size limit")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise ValueError("Invalid Amap JSON") from None
    return parse_results(payload, origins)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-requests", type=int, choices=(1, 2))
    args = parser.parse_args()
    if not args.live:
        if args.max_requests is not None:
            parser.error("--max-requests requires --live")
        print("Dry run. No Amap requests sent.")
        return
    if args.max_requests is None:
        parser.error("--live requires --max-requests")
    key = os.getenv("AMAP_WEB_SERVICE_KEY")
    if not key:
        parser.error("Set AMAP_WEB_SERVICE_KEY before running a live check")
    for request_index, origins in enumerate((ORIGINS[:1], ORIGINS)[:args.max_requests]):
        if request_index:
            time.sleep(1)
        try:
            results = probe(key, origins)
        except ValueError as error:
            parser.exit(1, f"{error}\n")
        print(json.dumps({
            "coordinate_system": "GCJ-02",
            "direction": "origins_to_destination",
            "origin_count": len(origins),
            "results": results,
        }), flush=True)
        if any(result["status"] != "ok" for result in results):
            parser.exit(1, "At least one origin did not return a valid result.\n")


if __name__ == "__main__":
    main()
