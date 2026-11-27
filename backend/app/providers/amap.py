import asyncio
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime

import httpx

from .amap_config import AmapRoutingConfig
from .amap_limiter import MAX_RETRY_WAIT_S, limiter_for
from .amap_metro import BEIJING, METRO_ENDPOINT, parse_metro, route_parameters
from .amap_response import ENDPOINT, check_success, parse_results
from .base import Capabilities, Point, TravelResult, validate_driving_request

# HTTPX logs request URLs at INFO, and the upstream requires the Key in the query.
logging.getLogger("httpx").setLevel(logging.WARNING)

RATE_LIMIT_CODES = {"10004", "10014", "10015", "10019", "10020", "10021", "10029"}
DAILY_LIMIT_CODES = {"10003", "10044", "10045"}
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class AmapProvider:
    def __init__(self, key: str, client: httpx.AsyncClient, config: AmapRoutingConfig | None = None) -> None:
        self.key = key
        self.client = client
        self.config = config if config is not None else AmapRoutingConfig.from_env()

    def capabilities(self) -> Capabilities:
        return Capabilities(frozenset({"driving"}), True, False, False, self.config.batch_size)

    async def travel_time_matrix(
        self, origins: list[Point], destination: Point, mode: str,
        departure_time: datetime | None = None, *,
        before_request: Callable[[], None] | None = None,
    ) -> list[TravelResult]:
        validate_driving_request(origins, mode, departure_time, max_origins=self.capabilities().max_origins)
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
        limiter = limiter_for(self.key, self.config)
        for attempt in range(MAX_ATTEMPTS):
            backoff = 2.0 ** attempt + random.uniform(0, 0.25)
            retry_after = 0.0
            async with limiter.request(before_request):
                try:
                    async with asyncio.timeout(15):
                        response = await self.client.get(endpoint, params=params)
                except (httpx.RequestError, TimeoutError):
                    error = "高德请求超时或连接失败"
                else:
                    retry_after = retry_delay(response.headers.get("Retry-After"))
                    if response.status_code == 200:
                        payload = response_json(response)
                        code = str(payload.get("infocode", "")) if isinstance(payload, dict) else ""
                        if not isinstance(payload, dict) or str(payload.get("status")) == "1":
                            return payload
                        if code not in RATE_LIMIT_CODES:
                            if code in DAILY_LIMIT_CODES:
                                limiter.block(until_next_amap_day(), f"Amap business error (infocode {code})")
                            return check_success(payload)
                        error = f"Amap business error (infocode {code})"
                        if code == "10004":
                            retry_after = max(60, retry_after)
                        limiter.cool_down(max(backoff, retry_after))
                    else:
                        error = f"高德 HTTP 请求失败（{response.status_code}）"
                        if response.status_code not in RETRYABLE_HTTP_STATUSES:
                            raise ValueError(error)
                        if response.status_code == 429 or retry_after:
                            limiter.cool_down(max(backoff, retry_after))
            if retry_after > MAX_RETRY_WAIT_S:
                raise ValueError(f"{error}，高德要求等待后再试，请稍后重新生成")
            if attempt == MAX_ATTEMPTS - 1 or before_request is None:
                raise ValueError(error)
            await asyncio.sleep(backoff)


def response_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except (ValueError, RecursionError):
        raise ValueError("高德返回了无效 JSON") from None


def until_next_amap_day() -> float:
    current = datetime.now(BEIJING)
    midnight = datetime.combine(current.date() + timedelta(days=1), time(), BEIJING)
    return (midnight - current).total_seconds()


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
