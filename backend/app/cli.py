import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime

from .providers.base import Point, TravelResult
from .providers.mock import MockProvider


async def generate(lon: float, lat: float) -> dict:
    destination = Point("destination", lon, lat)
    if abs(lat) > 89.99 or abs(lon) > 179.99:
        raise ValueError("Destination is too close to a coordinate boundary for the demo")
    origins = [
        Point("west", lon - 0.01, lat),
        Point("east", lon + 0.01, lat),
        Point("north", lon, lat + 0.01),
        Point("south", lon, lat - 0.01),
    ]
    provider = MockProvider(missing_ids=frozenset({"north"}))
    results = await provider.travel_time_matrix(origins, destination, "driving")
    center = TravelResult(destination.id, 0, 0, "ok", None, datetime.now(UTC))
    samples = []
    for point, result in zip([destination, *origins], [center, *results], strict=True):
        sample = asdict(result)
        sample["observed_at"] = result.observed_at.isoformat()
        sample["coordinates"] = [point.lon, point.lat]
        samples.append(sample)
    return {
        "project": "TISO",
        "provider": "mock",
        "simulated": True,
        "direction": "origins_to_destination",
        "coordinate_system": "GCJ-02",
        "coordinate_order": ["longitude", "latitude"],
        "destination": [lon, lat],
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a TISO reverse travel-time sample")
    parser.add_argument("--lon", type=float, default=121.4737)
    parser.add_argument("--lat", type=float, default=31.2304)
    args = parser.parse_args()
    try:
        result = asyncio.run(generate(args.lon, args.lat))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
