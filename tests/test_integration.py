import time
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

import app.main as main_module
import app.services.health_checker as health_checker_module


def test_scheduled_check_is_stored_and_queryable(monkeypatch):
    with TestClient(main_module.app) as cleanup_client:
        for endpoint in cleanup_client.get("/api/v1/endpoints").json():
            cleanup_client.delete(f"/api/v1/endpoints/{endpoint['id']}")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    real_async_client = httpx.AsyncClient

    def mock_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(health_checker_module.httpx, "AsyncClient", mock_async_client)
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(scheduler_enabled=True))

    with TestClient(main_module.app) as client:
        create_response = client.post(
            "/api/v1/endpoints",
            json={
                "url": "https://probe.test/health",
                "interval_seconds": 30,
                "expected_status_code": 200,
            },
        )
        assert create_response.status_code == 201, create_response.text
        endpoint_id = create_response.json()["id"]

        deadline = time.monotonic() + 5
        history = []
        while time.monotonic() < deadline:
            history_response = client.get(f"/api/v1/endpoints/{endpoint_id}/history")
            assert history_response.status_code == 200, history_response.text
            history = history_response.json()
            if history:
                break
            time.sleep(0.1)

        assert len(history) == 1
        assert history[0]["status_code"] == 503
        assert history[0]["is_healthy"] is False

        endpoint = next(
            item
            for item in client.get("/api/v1/endpoints").json()
            if item["id"] == endpoint_id
        )
        assert endpoint["current_state"] == "UNHEALTHY"
        assert endpoint["last_checked_at"] is not None

        transition_response = client.get(
            f"/api/v1/endpoints/{endpoint_id}/transitions"
        )
        assert transition_response.status_code == 200
        transitions = transition_response.json()
        assert len(transitions) == 1
        assert transitions[0]["previous_state"] == "HEALTHY"
        assert transitions[0]["new_state"] == "UNHEALTHY"
