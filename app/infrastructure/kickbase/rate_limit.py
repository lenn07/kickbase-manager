"""Token-Bucket-Rate-Limiter mit optionalem Jitter — schützt vor API-Bans."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque


class AsyncRateLimiter:
    def __init__(
        self,
        max_calls: int,
        period_s: float = 60.0,
        jitter: tuple[float, float] | None = None,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls muss > 0 sein")
        self._max_calls = max_calls
        self._period_s = period_s
        self._jitter = jitter
        self._lock = asyncio.Lock()
        self._calls: deque[float] = deque()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._period_s
            while self._calls and self._calls[0] < cutoff:
                self._calls.popleft()
            if len(self._calls) >= self._max_calls:
                wait = self._period_s - (now - self._calls[0])
                if wait > 0:
                    await asyncio.sleep(wait)
            self._calls.append(time.monotonic())

        if self._jitter is not None:
            lo, hi = self._jitter
            await asyncio.sleep(random.uniform(lo, hi))  # noqa: S311 — nur Jitter, kein Krypto
