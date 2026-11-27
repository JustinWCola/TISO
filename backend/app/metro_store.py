import asyncio
import gzip
import logging
import os
from itertools import chain
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from shapely import contains_xy, prepare
from shapely.geometry import shape

from .core.metro_network import MetroNetwork
from .providers.osm_metro import build_network, city_query, find_city, overpass
from .schemas import Destination

BUNDLED = Path(__file__).parent / "core" / "data" / "metro"
LOG = logging.getLogger(__name__)


def network_bytes(network: MetroNetwork) -> bytes:
    return gzip.compress(network.model_dump_json().encode(), mtime=0)


def save_network(directory: Path, network: MetroNetwork) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{network.city_id}.json.gz"
    with NamedTemporaryFile(dir=directory, delete=False) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(network_bytes(network))
            temporary.flush()
            os.fsync(temporary.fileno())
            path.replace(destination)
        finally:
            path.unlink(missing_ok=True)


class MetroStore:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.path = Path(os.getenv("TISO_METRO_DIR", ".tiso-metro"))
        self.networks: dict[int, MetroNetwork] = {}
        self.boundaries = {}
        self.task: asyncio.Task | None = None
        self.target: tuple[float, float] | None = None
        self.error: str | None = None
        for file in chain(BUNDLED.glob("*.json.gz"), self.path.glob("*.json.gz")):
            try:
                self.add(MetroNetwork.model_validate_json(gzip.decompress(file.read_bytes())))
            except (OSError, ValueError, TypeError):
                LOG.warning("Ignored invalid local metro data file %s", file.name)

    def add(self, network: MetroNetwork) -> None:
        boundary = shape(network.boundary)
        prepare(boundary)
        self.networks[network.city_id] = network
        self.boundaries[network.city_id] = boundary

    def lookup(self, destination: Destination) -> MetroNetwork | None:
        # A local city polygon makes repeat analysis independent of external geocoding.
        matches = [city for city, shape in self.boundaries.items()
                   if contains_xy(shape, destination.lng, destination.lat)]
        if matches:
            return self.networks[min(matches, key=lambda city: self.boundaries[city].area)]
        return None

    def status(self, destination: Destination) -> dict:
        network = self.lookup(destination)
        if network:
            return {"status": "ready", **network.summary()}
        if self.target == (destination.lng, destination.lat):
            if self.task and not self.task.done():
                return {"status": "building"}
            if self.error:
                return {"status": "failed", "message": self.error}
        return {"status": "missing"}

    def start(self, destination: Destination) -> dict:
        status = self.status(destination)
        if status["status"] in {"ready", "building"}:
            return status
        if self.task and not self.task.done():
            raise ValueError("已有城市地铁路网正在生成")
        self.target = (destination.lng, destination.lat)
        self.error = None
        self.task = asyncio.create_task(self.build(destination))
        return {"status": "building"}

    async def build(self, destination: Destination) -> None:
        try:
            city = await find_city(self.client, destination.lng, destination.lat)
            if city in self.networks:
                raise ValueError("现有路网的城市边界未覆盖当前目的地")
            payload = await overpass(self.client, city_query(city))
            network = await asyncio.to_thread(build_network, city, payload)
            if not network.contains(destination.lng, destination.lat):
                raise ValueError("生成的地铁路网未覆盖当前目的地")
            await asyncio.to_thread(save_network, self.path, network)
            self.add(network)
        except ValueError as error:
            self.error = str(error)
        except (OSError, KeyError, TypeError) as error:
            LOG.warning("Local metro build failed (%s)", type(error).__name__)
            self.error = "无法生成或保存地铁路网"
