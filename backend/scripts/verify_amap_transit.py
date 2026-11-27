"""Compare Amap metro responses for the same station pair at up to four departure times."""

import argparse
import asyncio
import json
import os
from datetime import datetime

import httpx

from app.providers.amap import AmapProvider
from app.providers.amap_metro import BEIJING, POI_ID
from app.providers.base import Point



def timestamp(value: str) -> datetime:
    try:
        departure = datetime.fromisoformat(value)
        if departure.utcoffset() is None or departure.second or departure.microsecond:
            raise ValueError
        return departure.astimezone(BEIJING)
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError("请提供包含时区偏移且精确到分钟的 ISO 8601 时间") from None


def station(value: str) -> Point:
    try:
        poi, lng, lat = value.split(",")
        if not POI_ID.fullmatch(poi):
            raise ValueError
        return Point(poi, float(lng), float(lat))
    except ValueError:
        raise argparse.ArgumentTypeError("请提供地铁站 POI_ID,经度,纬度") from None


async def probe(key: str, origin: Point, destination: Point, city: str, departures: list[datetime]) -> None:
    async with httpx.AsyncClient(follow_redirects=False) as client:
        provider = AmapProvider(key, client)
        for departure in departures:
            result = await provider.metro_departure(origin, destination, city, departure)
            print(json.dumps({
                "departure_time": departure.isoformat(), "status": result.status,
                "duration_s": result.duration_s, "distance_m": result.distance_m,
                "provider_error": result.provider_error, "observed_at": result.observed_at.isoformat(),
            }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-requests", type=int, choices=range(1, 5))
    parser.add_argument("--departure", type=timestamp, action="append", default=[])
    parser.add_argument("--origin", type=station)
    parser.add_argument("--destination", type=station)
    parser.add_argument("--city", default="021")
    args = parser.parse_args()
    if not args.live:
        if args.max_requests is not None:
            parser.error("--max-requests requires --live")
        print("Dry run. No Amap requests sent.")
        return
    if args.max_requests is None or not 1 <= len(args.departure) <= args.max_requests:
        parser.error("--live requires --max-requests and one --departure per request")
    if not args.origin or not args.destination or args.origin.id == args.destination.id:
        parser.error("--live requires distinct --origin and --destination stations")
    if not args.city.isascii() or not args.city.isdecimal() or len(args.city) not in (3, 4):
        parser.error("--city must be a 3–4 digit Amap citycode")
    key = os.getenv("AMAP_WEB_SERVICE_KEY")
    if not key:
        parser.error("Set AMAP_WEB_SERVICE_KEY before running a live check")
    try:
        asyncio.run(probe(key, args.origin, args.destination, args.city, args.departure))
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
