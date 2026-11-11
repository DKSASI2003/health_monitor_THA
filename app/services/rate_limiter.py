from __future__ import annotations

import asyncio


class RateLimiter:
    """Space outbound request starts at a fixed maximum rate without bursts."""

    def __init__(self, max_per_second: float) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be greater than zero")
        self._interval = 1 / max_per_second
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            delay = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._interval

        if delay:
            await asyncio.sleep(delay)
