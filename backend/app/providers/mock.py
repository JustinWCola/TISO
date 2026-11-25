import math
from datetime import UTC, datetime

from .base import (
    DRIVING_CAPABILITIES,
    Capabilities,
    Point,
    TravelResult,
    validate_driving_request,
)


class MockProvider:
    def __init__(self, missing_ids: frozenset[str] = frozenset(),
                 failed_ids: frozenset[str] = frozenset()) -> None:
        self.missing_ids = missing_ids
        self.failed_ids = failed_ids

    def capabilities(self) -> Capabilities:
        return DRIVING_CAPABILITIES

    async def travel_time_matrix(
        self, origins: list[Point], destination: Point, mode: str,
        departure_time: datetime | None = None,
    ) -> list[TravelResult]:
        validate_driving_request(origins, mode, departure_time)
        observed_at = datetime.now(UTC)
        metres_per_degree = math.pi * 6_371_008.8 / 180
        metres_per_longitude = metres_per_degree * math.cos(math.radians(destination.lat))
        results = []
        for point in origins:
            if point.id in self.failed_ids:
                results.append(TravelResult(point.id, None, None, "error", "mock_failure", observed_at))
                continue
            if point.id in self.missing_ids:
                results.append(TravelResult(point.id, None, None, "unreachable", None, observed_at))
                continue
            east = (point.lon - destination.lon) * metres_per_longitude
            north = (point.lat - destination.lat) * metres_per_degree
            distance = math.hypot(east, north)
            duration = distance / 8 + max(east, 0) / 12
            results.append(TravelResult(point.id, duration, distance, "ok", None, observed_at))
        return results
