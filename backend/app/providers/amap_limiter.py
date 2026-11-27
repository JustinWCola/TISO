import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from hashlib import sha256
from weakref import WeakKeyDictionary

from .amap_config import AmapRoutingConfig

MAX_RETRY_WAIT_S = 15


class AmapLimiter:
    def __init__(self, qps: int, max_concurrency: int) -> None:
        self.interval = 1 / qps
        self.max_concurrency = max_concurrency
        self.next_start = 0.0
        self.cooldown_until = 0.0
        self.blocked_until = 0.0
        self.block_error = ""
        self.in_flight = 0
        self.slot_released = asyncio.Event()

    def cool_down(self, seconds: float) -> None:
        self.cooldown_until = max(self.cooldown_until, asyncio.get_running_loop().time() + seconds)

    def block(self, seconds: float, message: str) -> None:
        self.blocked_until = asyncio.get_running_loop().time() + seconds
        self.block_error = message

    @asynccontextmanager
    async def request(self, before_request: Callable[[], None] | None):
        clock = asyncio.get_running_loop()
        while True:
            current = clock.time()
            if self.blocked_until > current:
                raise ValueError(self.block_error)
            if self.cooldown_until - current > MAX_RETRY_WAIT_S:
                raise ValueError("高德要求等待后再试，请稍后重新生成")
            if self.in_flight >= self.max_concurrency:
                await self.slot_released.wait()
                continue
            delay = max(self.next_start, self.cooldown_until) - current
            if delay > 0:
                await asyncio.sleep(delay)
                continue
            # The check, budget charge and admission have no suspension point.
            if before_request is not None:
                before_request()
            self.next_start = clock.time() + self.interval
            self.in_flight += 1
            break
        try:
            yield
        finally:
            self.in_flight -= 1
            self.slot_released.set()
            self.slot_released = asyncio.Event()


_limiters: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[bytes, AmapLimiter]] = WeakKeyDictionary()


def limiter_for(key: str, config: AmapRoutingConfig) -> AmapLimiter:
    # TISO runs all routing coroutines on its single server event loop.
    limiters = _limiters.setdefault(asyncio.get_running_loop(), {})
    key_id = sha256(key.encode()).digest()
    limiter = limiters.get(key_id)
    if limiter is None:
        limiter = limiters[key_id] = AmapLimiter(config.qps, config.max_concurrency)
    elif limiter.interval != 1 / config.qps or limiter.max_concurrency != config.max_concurrency:
        raise ValueError("同一高德 Web 服务 Key 须使用相同的限速配置")
    return limiter
