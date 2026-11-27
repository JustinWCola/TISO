import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from .amap_response import ENDPOINT, parse_results
from .amap_metro import METRO_ENDPOINT, parse_metro, route_parameters
from .base import DRIVING_CAPABILITIES, Capabilities, Point, TravelResult, validate_driving_request

# HTTPX logs request URLs at INFO, and the upstream requires the Key in the query.
logging.getLogger("httpx").setLevel(logging.WARNING)


class AmapProvider:
    def __init__(self, key: str, client: httpx.AsyncClient) -> None:
        self.key = key
        self.client = client
        self.next_request_at = 0.0

    def capabilities(self) -> Capabilities:
        return DRIVING_CAPABILITIES

    async def travel_time_matrix(
        self, origins: list[Point], destination: Point, mode: str,
        departure_time: datetime | None = None, *,
        before_request: Callable[[], None] | None = None,
    ) -> list[TravelResult]:
        validate_driving_request(origins, mode, departure_time)
        params = {
            "key": self.key,
            "origins": "|".join(f"{p.lon:.6f},{p.lat:.6f}" for p in origins),
            "destination": f"{destination.lon:.6f},{destination.lat:.6f}",
            "type": "1",
        }
        rows = parse_results(await self._get(ENDPOINT, params, before_request), origins)
        observed_at = datetime.now(UTC)
        return [TravelResult(
            row["point_id"], row.get("duration_s"), row.get("distance_m"),
            row["status"], row.get("provider_error"), observed_at,
        ) for row in rows]

    async def metro_departure(
        self, origin: Point, destination: Point, citycode: str, departure: datetime, *,
        before_request: Callable[[], None] | None = None,
    ) -> TravelResult:
        params = {"key": self.key, **route_parameters(origin, destination, citycode, departure)}
        return parse_metro(await self._get(METRO_ENDPOINT, params, before_request), origin)

    async def _get(self, endpoint: str, params: dict, before_request: Callable[[], None] | None) -> object:
        clock = asyncio.get_running_loop()
        for attempt in range(3):
            await asyncio.sleep(max(0, self.next_request_at - clock.time()))
            if before_request:
                before_request()
            self.next_request_at = clock.time() + 1
            try:
                async with asyncio.timeout(15):
                    response = await self.client.get(endpoint, params=params)
            except (httpx.RequestError, TimeoutError):
                if attempt == 2 or before_request is None:
                    raise ValueError("高德请求超时或连接失败") from None
            else:
                if response.status_code == 200:
                    break
                retryable = response.status_code in {429, 500, 502, 503, 504}
                if not retryable or attempt == 2 or before_request is None:
                    raise ValueError(f"高德 HTTP 请求失败（{response.status_code}）")
                delay = retry_delay(response.headers.get("Retry-After"))
                if delay > 15:
                    raise ValueError("高德要求等待后再试，请稍后重新生成")
                self.next_request_at = max(self.next_request_at, clock.time() + delay)
            await asyncio.sleep(1.0 * 2 ** attempt)
        try:
            return response.json()
        except (ValueError, RecursionError):
            raise ValueError("高德返回了无效 JSON") from None


def retry_delay(header: str | None) -> float:
    if not header:
        return 0
    try:
        if header.isascii() and header.isdecimal():
            return float(header)
        date = parsedate_to_datetime(header)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, OverflowError):
        return 0
