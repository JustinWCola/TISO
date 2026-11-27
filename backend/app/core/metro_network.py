import heapq
from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from shapely import contains_xy
from shapely.geometry import shape

from app.schemas import DEFAULT_WALK_SPEED_M_S

from .field import Grid

MAX_DESTINATION_STATIONS = 3
MAX_EGRESS_S = 1600 / DEFAULT_WALK_SPEED_M_S
BOARD_WAIT_S = 120


class Platform(BaseModel):
    id: str
    station: str
    name: str
    line: str
    lng: float = Field(ge=73, le=135)
    lat: float = Field(ge=18, le=54)


class MetroEdge(BaseModel):
    origin: str
    destination: str
    seconds: int = Field(gt=0, le=7200)
    kind: Literal["ride", "transfer"]


class MetroNetwork(BaseModel):
    version: Literal[1] = 1
    city_id: int = Field(gt=0)
    city_name: str
    boundary: dict
    source: Literal["osm"] = "osm"
    data_date: datetime | None = None
    platforms: list[Platform]
    edges: list[MetroEdge]

    @model_validator(mode="after")
    def validate_graph(self):
        ids = {platform.id for platform in self.platforms}
        if len(ids) != len(self.platforms) or not ids or not any(edge.kind == "ride" for edge in self.edges):
            raise ValueError("地铁路网缺少有效车站或站间时间")
        if any(edge.origin not in ids or edge.destination not in ids or edge.origin == edge.destination
               for edge in self.edges):
            raise ValueError("地铁路网含有无效的站间连接")
        boundary = shape(self.boundary)
        if boundary.geom_type not in {"Polygon", "MultiPolygon"} or boundary.is_empty or not boundary.is_valid:
            raise ValueError("城市边界无效")
        return self

    def contains(self, lng: float, lat: float) -> bool:
        return bool(contains_xy(shape(self.boundary), lng, lat))

    def summary(self) -> dict:
        return {
            "city_id": self.city_id, "city_name": self.city_name, "source": self.source,
            "station_count": len({platform.station for platform in self.platforms}),
            "ride_edges": sum(edge.kind == "ride" for edge in self.edges),
            "transfer_edges": sum(edge.kind == "transfer" for edge in self.edges),
            "data_date": self.data_date.isoformat() if self.data_date else None,
        }


def reverse_rides(network: MetroNetwork, grid: Grid, limit_s: float) -> tuple[dict[str, float], list[str]]:
    """Compute station-to-destination durations. Track whether a journey has used a train."""
    origins = defaultdict(list)
    for edge in network.edges:
        origins[edge.destination].append(edge)
    stations = defaultdict(list)
    for platform in network.platforms:
        stations[platform.station].append(platform)

    def egress(platform: Platform) -> float:
        x, y = grid.local_meters(platform.lng, platform.lat)
        return (x*x + y*y)**0.5 / grid.params.walk_speed_m_s

    nearest = sorted(stations.values(), key=lambda platforms: min(map(egress, platforms)))
    seeds = [platforms for platforms in nearest[:MAX_DESTINATION_STATIONS]
             if min(map(egress, platforms)) <= min(MAX_EGRESS_S, limit_s - BOARD_WAIT_S)]
    queue = [(egress(platform), platform.id, False) for group in seeds for platform in group]
    heapq.heapify(queue)
    settled = {}
    while queue:
        elapsed, platform, rode = heapq.heappop(queue)
        state = (platform, rode)
        if state in settled:
            continue
        settled[state] = elapsed
        for edge in origins[platform]:
            cost = elapsed + edge.seconds
            next_state = (edge.origin, rode or edge.kind == "ride")
            if cost + BOARD_WAIT_S <= limit_s and next_state not in settled:
                heapq.heappush(queue, (cost, *next_state))
    return {station: min(settled[platform.id, True] + BOARD_WAIT_S for platform in platforms
                         if (platform.id, True) in settled)
            for station, platforms in stations.items() if any((platform.id, True) in settled for platform in platforms)}, [
        group[0].name for group in seeds
    ]
