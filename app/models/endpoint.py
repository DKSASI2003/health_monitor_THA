from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class MonitoredEndpoint(Base):
    __tablename__ = "monitored_endpoints"
    __table_args__ = (UniqueConstraint("url", name="uq_monitored_endpoints_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    expected_status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    current_state: Mapped[str] = mapped_column(String(32), nullable=False, default="HEALTHY")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    results: Mapped[list["HealthCheckResult"]] = relationship(back_populates="endpoint", cascade="all, delete-orphan")
    transitions: Mapped[list["StateTransition"]] = relationship(back_populates="endpoint", cascade="all, delete-orphan")
