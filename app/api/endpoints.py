from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db
from app.models.endpoint import MonitoredEndpoint
from app.models.health_result import HealthCheckResult
from app.models.transition import StateTransition

router = APIRouter(prefix="/api/v1", tags=["endpoints"])


class EndpointCreate(BaseModel):
    url: AnyHttpUrl
    interval_seconds: int = Field(default=30, gt=0, le=86400)
    expected_status_code: int = Field(default=200, ge=100, le=599)


class EndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    interval_seconds: int
    expected_status_code: int
    current_state: str
    last_checked_at: datetime | None = None
    created_at: datetime


class HealthResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    endpoint_id: int
    status_code: int | None
    response_time_ms: int
    is_healthy: bool
    error_message: str | None = None
    checked_at: datetime


class TransitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    endpoint_id: int
    previous_state: str | None = None
    new_state: str
    changed_at: datetime


@router.post("/endpoints", response_model=EndpointRead, status_code=status.HTTP_201_CREATED)
async def register_endpoint(payload: EndpointCreate, session: AsyncSession = Depends(get_db)) -> MonitoredEndpoint:
    normalized_url = str(payload.url).rstrip("/")
    existing = await session.execute(
        select(MonitoredEndpoint.id).where(MonitoredEndpoint.url == normalized_url)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An endpoint with this URL is already registered",
        )

    endpoint = MonitoredEndpoint(
        url=normalized_url,
        interval_seconds=payload.interval_seconds,
        expected_status_code=payload.expected_status_code,
        current_state="HEALTHY",
    )
    session.add(endpoint)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An endpoint with this URL is already registered",
        ) from exc
    await session.refresh(endpoint)
    return endpoint


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(endpoint_id: int, session: AsyncSession = Depends(get_db)) -> None:
    endpoint = await session.get(MonitoredEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    await session.execute(delete(HealthCheckResult).where(HealthCheckResult.endpoint_id == endpoint_id))
    await session.execute(delete(StateTransition).where(StateTransition.endpoint_id == endpoint_id))
    await session.delete(endpoint)
    await session.commit()


@router.get("/endpoints", response_model=list[EndpointRead])
async def list_endpoints(session: AsyncSession = Depends(get_db)) -> list[MonitoredEndpoint]:
    result = await session.execute(select(MonitoredEndpoint).order_by(MonitoredEndpoint.created_at.desc()))
    return result.scalars().all()


@router.get("/endpoints/{endpoint_id}/history", response_model=list[HealthResultRead])
async def endpoint_history(
    endpoint_id: int,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[HealthCheckResult]:
    endpoint = await session.get(MonitoredEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    if start_time is not None and start_time.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_time must include a timezone",
        )
    if end_time is not None and end_time.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="end_time must include a timezone",
        )
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_time must be earlier than end_time",
        )

    query = select(HealthCheckResult).where(HealthCheckResult.endpoint_id == endpoint_id)
    if start_time is not None:
        query = query.where(
            HealthCheckResult.checked_at >= start_time.astimezone(timezone.utc)
        )
    if end_time is not None:
        query = query.where(
            HealthCheckResult.checked_at < end_time.astimezone(timezone.utc)
        )

    result = await session.execute(query.order_by(HealthCheckResult.checked_at.desc()))
    return result.scalars().all()


@router.get("/endpoints/{endpoint_id}/transitions", response_model=list[TransitionRead])
async def endpoint_transitions(endpoint_id: int, session: AsyncSession = Depends(get_db)) -> list[StateTransition]:
    endpoint = await session.get(MonitoredEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    result = await session.execute(
        select(StateTransition).where(StateTransition.endpoint_id == endpoint_id).order_by(StateTransition.changed_at.desc())
    )
    return result.scalars().all()
