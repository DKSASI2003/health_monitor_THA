import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database.connection import Base
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult
from app.models.transition import StateTransition
from app.services.scheduler import Scheduler


def test_stop_waits_for_in_flight_checks():
    async def run_test():
        check_started = asyncio.Event()
        allow_check_to_finish = asyncio.Event()
        check_finished = asyncio.Event()

        class SlowHealthChecker:
            async def execute_check(self, endpoint):
                check_started.set()
                await allow_check_to_finish.wait()
                check_finished.set()

        scheduler = Scheduler(health_checker=SlowHealthChecker())
        endpoint = SimpleNamespace(id=1, url="https://example.com")
        scheduler._schedule_check(endpoint)
        await check_started.wait()

        stop_task = asyncio.create_task(scheduler.stop())
        await asyncio.sleep(0)
        assert not stop_task.done()
        assert not check_finished.is_set()

        allow_check_to_finish.set()
        await stop_task

        assert check_finished.is_set()
        assert not scheduler._in_flight
        assert not scheduler._check_tasks

    asyncio.run(run_test())


def test_retention_cleanup_deletes_expired_results_but_keeps_recent_results_and_transitions():
    async def run_test():
        engine = create_async_engine("sqlite+aiosqlite://")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        old_time = datetime.now(timezone.utc) - timedelta(
            days=settings.check_result_retention_days + 1
        )
        recent_time = datetime.now(timezone.utc)
        async with session_factory() as session:
            endpoint = MonitoredEndpoint(
                url="https://retention.test",
                interval_seconds=30,
                expected_status_code=200,
                current_state="UNHEALTHY",
            )
            session.add(endpoint)
            await session.flush()
            endpoint_id = endpoint.id
            session.add_all(
                [
                    HealthCheckResult(
                        endpoint_id=endpoint_id,
                        status_code=500,
                        response_time_ms=12,
                        is_healthy=False,
                        checked_at=old_time,
                    ),
                    HealthCheckResult(
                        endpoint_id=endpoint_id,
                        status_code=200,
                        response_time_ms=9,
                        is_healthy=True,
                        checked_at=recent_time,
                    ),
                    StateTransition(
                        endpoint_id=endpoint_id,
                        previous_state="HEALTHY",
                        new_state="UNHEALTHY",
                        changed_at=old_time,
                    ),
                ]
            )
            await session.commit()

        scheduler = Scheduler()
        scheduler.session_factory = session_factory
        deleted_count = await scheduler._cleanup_expired_results()

        async with session_factory() as session:
            results = (
                await session.execute(
                    select(HealthCheckResult).where(
                        HealthCheckResult.endpoint_id == endpoint_id
                    )
                )
            ).scalars().all()
            transitions = (
                await session.execute(
                    select(StateTransition).where(StateTransition.endpoint_id == endpoint_id)
                )
            ).scalars().all()

        await engine.dispose()
        assert deleted_count == 1
        assert len(results) == 1
        assert results[0].is_healthy is True
        assert len(transitions) == 1

    asyncio.run(run_test())
