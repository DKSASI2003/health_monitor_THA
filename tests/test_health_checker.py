import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.health_checker as health_checker_module
from app.database.connection import Base
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult
from app.models.transition import StateTransition
from app.services.health_checker import HealthChecker
from app.services.state_manager import StateManager


async def run_check(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response_status: int | None,
    request_error: bool = False,
) -> tuple[str, list[HealthCheckResult], list[StateTransition]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        endpoint = MonitoredEndpoint(
            url="https://probe.test/health",
            interval_seconds=30,
            expected_status_code=200,
            current_state="HEALTHY",
        )
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)
        endpoint_id = endpoint.id

    async def handler(request: httpx.Request) -> httpx.Response:
        if request_error:
            raise httpx.ConnectError("connection failed", request=request)
        assert response_status is not None
        return httpx.Response(response_status, request=request)

    real_async_client = httpx.AsyncClient

    def mock_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(health_checker_module.httpx, "AsyncClient", mock_async_client)
    state_manager = StateManager()
    state_manager.session_factory = session_factory
    checker = HealthChecker(state_manager=state_manager)
    await checker.execute_check(endpoint)

    async with session_factory() as session:
        updated_endpoint = await session.get(MonitoredEndpoint, endpoint_id)
        results = (
            await session.execute(
                select(HealthCheckResult).where(HealthCheckResult.endpoint_id == endpoint_id)
            )
        ).scalars().all()
        transitions = (
            await session.execute(
                select(StateTransition).where(StateTransition.endpoint_id == endpoint_id)
            )
        ).scalars().all()

        assert updated_endpoint is not None
        current_state = updated_endpoint.current_state

    await engine.dispose()
    return current_state, results, transitions


def test_health_checker_records_expected_status_as_healthy(monkeypatch):
    current_state, results, transitions = asyncio.run(
        run_check(monkeypatch, response_status=200)
    )

    assert current_state == "HEALTHY"
    assert len(results) == 1
    assert results[0].status_code == 200
    assert results[0].is_healthy is True
    assert transitions == []


def test_health_checker_records_unexpected_status_and_transition(monkeypatch):
    current_state, results, transitions = asyncio.run(
        run_check(monkeypatch, response_status=503)
    )

    assert current_state == "UNHEALTHY"
    assert len(results) == 1
    assert results[0].status_code == 503
    assert results[0].is_healthy is False
    assert len(transitions) == 1
    assert transitions[0].previous_state == "HEALTHY"
    assert transitions[0].new_state == "UNHEALTHY"


def test_health_checker_records_connection_error_as_unhealthy(monkeypatch):
    current_state, results, transitions = asyncio.run(
        run_check(monkeypatch, response_status=None, request_error=True)
    )

    assert current_state == "UNHEALTHY"
    assert len(results) == 1
    assert results[0].status_code is None
    assert results[0].is_healthy is False
    assert results[0].error_message == "connection failed"
    assert len(transitions) == 1
    assert transitions[0].new_state == "UNHEALTHY"
