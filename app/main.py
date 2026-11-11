from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.endpoints import router
from app.config import settings
from app.database.connection import get_db, init_db
from app.monitoring.metrics import metrics_content_type, metrics_payload
from app.services.health_checker import HealthChecker
from app.services.scheduler import Scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.scheduler = Scheduler(health_checker=HealthChecker())
    if settings.scheduler_enabled:
        app.state.scheduler.start()
    yield
    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.stop()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await session.execute(select(1))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return {"status": "ready"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
