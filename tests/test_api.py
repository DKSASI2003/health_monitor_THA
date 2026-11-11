import os
import asyncio
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.endpoints import endpoint_history
from app.database.connection import Base
from app.main import app
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        for endpoint in test_client.get("/api/v1/endpoints").json():
            test_client.delete(f"/api/v1/endpoints/{endpoint['id']}")
        yield test_client


def test_register_and_list_endpoint(client):
    payload = {
        "url": "https://example.com",
        "interval_seconds": 30,
        "expected_status_code": 200,
    }

    response = client.post("/api/v1/endpoints", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["url"] == "https://example.com"
    assert data["current_state"] == "HEALTHY"

    list_response = client.get("/api/v1/endpoints")
    assert list_response.status_code == 200, list_response.text
    items = list_response.json()
    assert len(items) == 1
    assert items[0]["id"] == data["id"]


def test_duplicate_url_is_rejected_even_with_different_settings(client):
    first_response = client.post(
        "/api/v1/endpoints",
        json={
            "url": "https://example.com",
            "interval_seconds": 30,
            "expected_status_code": 200,
        },
    )
    duplicate_response = client.post(
        "/api/v1/endpoints",
        json={
            "url": "https://example.com/",
            "interval_seconds": 60,
            "expected_status_code": 204,
        },
    )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"] == "An endpoint with this URL is already registered"
    assert len(client.get("/api/v1/endpoints").json()) == 1


def test_history_and_transitions_are_empty_for_new_endpoint(client):
    create_response = client.post(
        "/api/v1/endpoints",
        json={"url": "https://example.com", "interval_seconds": 15, "expected_status_code": 200},
    )
    endpoint_id = create_response.json()["id"]

    history_response = client.get(f"/api/v1/endpoints/{endpoint_id}/history")
    transitions_response = client.get(f"/api/v1/endpoints/{endpoint_id}/transitions")

    assert history_response.status_code == 200
    assert transitions_response.status_code == 200
    assert history_response.json() == []
    assert transitions_response.json() == []


def test_history_filters_include_start_and_exclude_end():
    async def run_test():
        engine = create_async_engine("sqlite+aiosqlite://")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_time = start_time + timedelta(hours=2)
        async with session_factory() as session:
            endpoint = MonitoredEndpoint(
                url="https://history.test",
                interval_seconds=30,
                expected_status_code=200,
                current_state="HEALTHY",
            )
            session.add(endpoint)
            await session.flush()
            endpoint_id = endpoint.id
            session.add_all(
                [
                    HealthCheckResult(
                        endpoint_id=endpoint_id,
                        status_code=200,
                        response_time_ms=10,
                        is_healthy=True,
                        checked_at=start_time - timedelta(seconds=1),
                    ),
                    HealthCheckResult(
                        endpoint_id=endpoint_id,
                        status_code=200,
                        response_time_ms=11,
                        is_healthy=True,
                        checked_at=start_time,
                    ),
                    HealthCheckResult(
                        endpoint_id=endpoint_id,
                        status_code=200,
                        response_time_ms=12,
                        is_healthy=True,
                        checked_at=start_time + timedelta(hours=1),
                    ),
                    HealthCheckResult(
                        endpoint_id=endpoint_id,
                        status_code=200,
                        response_time_ms=13,
                        is_healthy=True,
                        checked_at=end_time,
                    ),
                ]
            )
            await session.commit()

            filtered = await endpoint_history(
                endpoint_id,
                start_time=start_time,
                end_time=end_time,
                session=session,
            )
            assert [item.response_time_ms for item in filtered] == [12, 11]

        await engine.dispose()

    asyncio.run(run_test())


def test_history_rejects_naive_or_reversed_time_ranges(client):
    create_response = client.post(
        "/api/v1/endpoints",
        json={"url": "https://history-validation.test", "interval_seconds": 30},
    )
    endpoint_id = create_response.json()["id"]

    naive_response = client.get(
        f"/api/v1/endpoints/{endpoint_id}/history",
        params={"start_time": "2026-01-01T00:00:00"},
    )
    reversed_response = client.get(
        f"/api/v1/endpoints/{endpoint_id}/history",
        params={
            "start_time": "2026-01-02T00:00:00Z",
            "end_time": "2026-01-01T00:00:00Z",
        },
    )

    assert naive_response.status_code == 422
    assert reversed_response.status_code == 422


def test_health_and_ready_endpoints(client):
    health_response = client.get("/health")
    ready_response = client.get("/ready")
    versioned_health_response = client.get("/api/v1/health")
    versioned_ready_response = client.get("/api/v1/ready")

    assert health_response.status_code == 200
    assert ready_response.status_code == 200
    assert versioned_health_response.status_code == 404
    assert versioned_ready_response.status_code == 404
    assert health_response.json() == {"status": "ok"}
    assert ready_response.json() == {"status": "ready"}
