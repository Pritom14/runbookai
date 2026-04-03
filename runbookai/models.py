"""SQLAlchemy models for RunbookAI."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

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
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    messages_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Regression detection — set when this incident fires shortly after a
    # prior remediation (e.g. restart) on the same service.
    possible_regression: Mapped[bool] = mapped_column(default=False)
    prior_incident_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    actions: Mapped[list["AgentAction"]] = relationship(back_populates="incident")
    approvals: Mapped[list["ApprovalRequest"]] = relationship(back_populates="incident")


class AgentAction(Base):
    """A single tool call made by the agent during incident response."""

    __tablename__ = "agent_actions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    tool_name: Mapped[str] = mapped_column(String)
    tool_input: Mapped[dict] = mapped_column(JSON)
    tool_output: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    incident: Mapped["Incident"] = relationship(back_populates="actions")


class Runbook(Base):
    """A runbook stored in the database, matched by alert name substring."""

    __tablename__ = "runbooks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True)
    alert_pattern: Mapped[str] = mapped_column(String)  # substring match on alert_name
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    incident: Mapped["Incident"] = relationship(back_populates="approvals")


class HostCredential(Base):
    """SSH credentials for a remote host.

    Stored per-hostname. The agent looks up credentials here before every
    SSH connection. Falls back to global settings (SSH_DEFAULT_USERNAME,
    SSH_PRIVATE_KEY_PATH) when no row matches.

    private_key_pem stores the raw PEM string of the SSH private key.
    Never log or expose this field.
    """

    __tablename__ = "host_credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    hostname: Mapped[str] = mapped_column(String, unique=True, index=True)
    username: Mapped[str] = mapped_column(String)
    private_key_pem: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    port: Mapped[int] = mapped_column(default=22)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
