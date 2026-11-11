from __future__ import annotations

from datetime import datetime, timezone

from app.database.connection import async_session
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult
from app.models.transition import StateTransition


class StateManager:
    def __init__(self) -> None:
        self.session_factory = async_session

    async def apply_result(
        self,
        endpoint: MonitoredEndpoint,
        result: HealthCheckResult,
    ) -> bool | None:
        async with self.session_factory() as session:
            stored_endpoint = await session.get(MonitoredEndpoint, endpoint.id)
            if stored_endpoint is None:
                return None

            new_state = "HEALTHY" if result.is_healthy else "UNHEALTHY"
            previous_state = stored_endpoint.current_state
            stored_endpoint.current_state = new_state
            stored_endpoint.last_checked_at = datetime.now(timezone.utc)

            if previous_state != new_state:
                transition = StateTransition(
                    endpoint_id=stored_endpoint.id,
                    previous_state=previous_state,
                    new_state=new_state,
                )
                session.add(transition)

            session.add(result)
            await session.commit()
            return previous_state != new_state
