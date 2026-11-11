import asyncio

import pytest

from app.services.rate_limiter import RateLimiter


def test_rate_limiter_spaces_outbound_starts():
    async def run_test():
        limiter = RateLimiter(max_per_second=20)
        start_times = []

        async def make_request():
            await limiter.acquire()
            start_times.append(asyncio.get_running_loop().time())

        await asyncio.gather(*(make_request() for _ in range(3)))
        start_times.sort()

        assert len(start_times) == 3
        assert start_times[1] - start_times[0] >= 0.04
        assert start_times[2] - start_times[1] >= 0.04

    asyncio.run(run_test())


def test_rate_limiter_rejects_non_positive_rate():
    with pytest.raises(ValueError, match="greater than zero"):
        RateLimiter(max_per_second=0)
