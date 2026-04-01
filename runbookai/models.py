"""SQLAlchemy models for RunbookAI."""

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IncidentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_APPROVAL = "waiting_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String)  # "pagerduty" | "generic"
    alert_name: Mapped[str] = mapped_column(String)
    alert_body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default=IncidentStatus.PENDING)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    actions: Mapped[list["AgentAction"]] = relationship(back_populates="incident")
    approvals: Mapped[list["ApprovalRequest"]] = relationship(back_populates="incident")


class AgentAction(Base):
    """A single tool call made by the agent during incident response."""

    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    tool_name: Mapped[str] = mapped_column(String)
    tool_input: Mapped[dict] = mapped_column(JSON)
    tool_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    incident: Mapped["Incident"] = relationship(back_populates="actions")


class ApprovalRequest(Base):
    """Pending action waiting for human approval (Suggest Mode)."""

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    tool_name: Mapped[str] = mapped_column(String)
    tool_input: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)  # why the agent wants to do this
    status: Mapped[str] = mapped_column(String, default=ApprovalStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    incident: Mapped["Incident"] = relationship(back_populates="approvals")
