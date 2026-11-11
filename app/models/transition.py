from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class StateTransition(Base):
    __tablename__ = "state_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("monitored_endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    endpoint: Mapped["MonitoredEndpoint"] = relationship(back_populates="transitions")
