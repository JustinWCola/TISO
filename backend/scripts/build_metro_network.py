"""Build a local metro graph from OpenStreetMap route relations."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from app.metro_store import save_network
from app.providers.osm_metro import SHANGHAI_ID, build_network, city_query, overpass


async def fetch_city(city: int) -> dict:
    async with httpx.AsyncClient(follow_redirects=False) as client:
        return await overpass(client, city_query(city))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", type=int, default=SHANGHAI_ID, help="OpenStreetMap city relation ID")
    parser.add_argument("--overpass-json", type=Path, help="Use a previously downloaded Overpass response")
    parser.add_argument("--directory", type=Path, help="Destination directory; defaults to TISO_METRO_DIR")
    args = parser.parse_args()
    payload = (json.loads(args.overpass_json.read_bytes()) if args.overpass_json
               else asyncio.run(fetch_city(args.city)))
    network = build_network(args.city, payload)
    directory = args.directory or Path(os.getenv("TISO_METRO_DIR", ".tiso-metro"))
    save_network(directory, network)
    print(json.dumps(network.summary(), ensure_ascii=False))


if __name__ == "__main__":
    main()
