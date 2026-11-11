from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult
from app.monitoring.logging import logger
from app.monitoring.metrics import (
    check_latency_ms,
    check_persistence_errors_total,
    checks_total,
    failed_checks_total,
    health_state_changes_total,
    last_check_completion_timestamp_seconds,
)
from app.services.rate_limiter import RateLimiter
from app.services.state_manager import StateManager


class HealthChecker:
    def __init__(self, state_manager: StateManager | None = None) -> None:
        self.state_manager = state_manager or StateManager()
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        self.rate_limiter = RateLimiter(settings.max_checks_per_second)

    async def execute_check(self, endpoint: MonitoredEndpoint) -> None:
        async with self.semaphore:
            await self.rate_limiter.acquire()
            start = time.perf_counter()
            status_code = None
            error_message = None
            is_healthy = False
            response_time_ms = 0

            try:
                async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
                    response = await client.get(endpoint.url)
                    status_code = response.status_code
                    response_time_ms = int((time.perf_counter() - start) * 1000)
                    is_healthy = response.status_code == endpoint.expected_status_code
            except httpx.HTTPError as exc:
                response_time_ms = int((time.perf_counter() - start) * 1000)
                error_message = str(exc)
                is_healthy = False
                logger.warning("Health check failed for %s: %s", endpoint.url, exc)
            except Exception as exc:  # pragma: no cover - defensive
                response_time_ms = int((time.perf_counter() - start) * 1000)
                error_message = str(exc)
                is_healthy = False
                logger.exception("Unexpected health check error for %s", endpoint.url)

            check_latency_ms.observe(response_time_ms)
            checks_total.inc()
            if not is_healthy:
                failed_checks_total.inc()

            result = HealthCheckResult(
                endpoint_id=endpoint.id,
                status_code=status_code,
                response_time_ms=response_time_ms,
                is_healthy=is_healthy,
                error_message=error_message,
                checked_at=datetime.now(timezone.utc),
            )

            try:
                state_changed = await self.state_manager.apply_result(endpoint, result)
            except Exception:
                check_persistence_errors_total.inc()
                raise

            if state_changed is None:
                return

            last_check_completion_timestamp_seconds.set(time.time())
            if state_changed:
                health_state_changes_total.inc()

            logger.info(
                "Checked %s -> status=%s healthy=%s response_ms=%s",
                endpoint.url,
                status_code,
                is_healthy,
                response_time_ms,
            )
