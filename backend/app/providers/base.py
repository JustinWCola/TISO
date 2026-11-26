from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class Point:
    id: str
    lon: float
    lat: float

    def __post_init__(self) -> None:
        if not -180 <= self.lon <= 180 or not -90 <= self.lat <= 90:
            raise ValueError("Expected longitude and latitude in degrees")


@dataclass(frozen=True)
class Capabilities:
    modes: frozenset[str]
    supports_matrix: bool
    supports_departure_time: bool
    supports_arrival_time: bool
    max_origins: int
    coordinate_system: str = "GCJ-02"


DRIVING_CAPABILITIES = Capabilities(frozenset({"driving"}), True, False, False, 20)


@dataclass(frozen=True)
class TravelResult:
    point_id: str
    duration_s: float | None
    distance_m: float | None
    status: Literal["ok", "unreachable", "error", "outside_scope"]
    provider_error: str | None
    observed_at: datetime


class RoutingProvider(Protocol):
    def capabilities(self) -> Capabilities: ...

    async def travel_time_matrix(
        self, origins: list[Point], destination: Point, mode: str,
        departure_time: datetime | None = None,
    ) -> list[TravelResult]: ...


def validate_driving_request(
    origins: list[Point], mode: str, departure_time: datetime | None,
) -> None:
    if mode != "driving" or departure_time is not None:
        raise ValueError("Only driving with no departure time is supported")
    if not 1 <= len(origins) <= DRIVING_CAPABILITIES.max_origins:
        raise ValueError("Expected 1 to 20 origins per request")
