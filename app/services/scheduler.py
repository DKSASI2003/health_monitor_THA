from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.database.connection import async_session
from app.config import settings
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult
from app.monitoring.logging import logger
from app.monitoring.metrics import (
    active_endpoints,
    max_endpoint_interval_seconds,
    retention_cleanup_errors_total,
    retention_deleted_results_total,
    scheduler_last_scan_timestamp_seconds,
    scheduler_scan_errors_total,
    unhealthy_endpoints,
)
from app.services.health_checker import HealthChecker


class Scheduler:
    def __init__(self, health_checker: HealthChecker | None = None) -> None:
        self.health_checker = health_checker or HealthChecker()
        self.session_factory = async_session
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._in_flight: set[int] = set()
        self._check_tasks: set[asyncio.Task[None]] = set()
        self._next_retention_cleanup = time.monotonic() + 24 * 60 * 60

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._check_tasks:
            logger.info("Waiting for %d in-flight health checks to finish", len(self._check_tasks))
            await asyncio.gather(*self._check_tasks)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._run_retention_cleanup_if_due()
            try:
                await self._scan_and_run_due_checks()
            except asyncio.CancelledError:
                raise
            except Exception:
                scheduler_scan_errors_total.inc()
                logger.exception("Scheduler scan failed; will retry")

            await asyncio.sleep(1)

    async def _run_retention_cleanup_if_due(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._next_retention_cleanup:
            return

        try:
            deleted_count = await self._cleanup_expired_results()
        except asyncio.CancelledError:
            raise
        except Exception:
            retention_cleanup_errors_total.inc()
            logger.exception("Health check result retention cleanup failed")
            self._next_retention_cleanup = now + 60
            return

        retention_deleted_results_total.inc(deleted_count)
        logger.info("Retention cleanup deleted %d health check results", deleted_count)
        self._next_retention_cleanup = loop.time() + 24 * 60 * 60

    async def _cleanup_expired_results(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.check_result_retention_days)
        async with self.session_factory() as session:
            result = await session.execute(
                delete(HealthCheckResult).where(HealthCheckResult.checked_at < cutoff)
            )
            await session.commit()
            return result.rowcount or 0

    async def _scan_and_run_due_checks(self) -> None:
        async with self.session_factory() as session:
            rows = (await session.execute(select(MonitoredEndpoint))).scalars().all()

        active_endpoints.set(len(rows))
        unhealthy_endpoints.set(sum(endpoint.current_state == "UNHEALTHY" for endpoint in rows))
        max_endpoint_interval_seconds.set(
            max((endpoint.interval_seconds for endpoint in rows), default=0)
        )
        scheduler_last_scan_timestamp_seconds.set(time.time())

        now = datetime.now(timezone.utc)
        due: list[MonitoredEndpoint] = []
        for endpoint in rows:
            if endpoint.id in self._in_flight:
                continue
            last_checked_at = endpoint.last_checked_at
            if last_checked_at is None:
                due.append(endpoint)
                continue
            if last_checked_at.tzinfo is None:
                last_checked_at = last_checked_at.replace(tzinfo=timezone.utc)
            if now - last_checked_at >= timedelta(seconds=endpoint.interval_seconds):
                due.append(endpoint)

        for endpoint in due:
            self._schedule_check(endpoint)

    def _schedule_check(self, endpoint: MonitoredEndpoint) -> None:
        self._in_flight.add(endpoint.id)
        task = asyncio.create_task(self._run_single(endpoint))
        self._check_tasks.add(task)
        task.add_done_callback(self._check_tasks.discard)

    async def _run_single(self, endpoint: MonitoredEndpoint) -> None:
        try:
            await self.health_checker.execute_check(endpoint)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Health check could not be completed for endpoint id=%s url=%s",
                endpoint.id,
                endpoint.url,
            )
        finally:
            self._in_flight.discard(endpoint.id)
