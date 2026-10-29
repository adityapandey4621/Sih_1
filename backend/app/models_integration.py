"""The integration log: one row per call made across the integration boundary. Append-only.

It records THAT a call happened and how it went (outcome code, HTTP status, latency) and the hash of the message - never a secret, a header, a body or a document.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import new_id, utcnow


class IntegrationLog(Base):
    __tablename__ = "integration_logs"
    __table_args__ = (Index("ix_integration_logs_sync", "sync_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sync_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("external_syncs.id"))
    adapter: Mapped[str] = mapped_column(String(20))
    operation: Mapped[str] = mapped_column(String(12))  # submit | status
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    outcome: Mapped[str] = mapped_column(String(30))
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    payload_hash: Mapped[str] = mapped_column(String(64))
