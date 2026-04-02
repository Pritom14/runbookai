"""Tests for webhook parsing and DB integration, and approval endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from runbookai.integrations.pagerduty import parse_pagerduty_payload
from runbookai.models import ApprovalRequest, ApprovalStatus, Base, Incident, IncidentStatus

# ---------------------------------------------------------------------------
# In-memory SQLite engine shared across all tests in this module.
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(_TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSessionLocal = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _override_get_session():
    async with _TestSessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db_and_override():
    """Create tables, apply the in-memory DB override, and clean up after each test."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from runbookai.database import get_session
    from runbookai.main import app

    app.dependency_overrides[get_session] = _override_get_session
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app():
    from runbookai.main import app
    return app


# ---------------------------------------------------------------------------
# PagerDuty payload parsing (pure unit tests — no DB)
# ---------------------------------------------------------------------------


def test_parse_pagerduty_non_trigger_event_ignored():
    payload = {"event": {"event_type": "incident.resolved", "data": {}}}
    result = parse_pagerduty_payload(payload)
    assert result == {}


def test_parse_pagerduty_trigger_event():
    payload = {
        "event": {
            "event_type": "incident.triggered",
            "data": {
                "title": "DB connection pool exhausted",
                "service": {"name": "payments-api"},
                "urgency": "high",
                "description": "Max connections reached",
            },
        }
    }
    result = parse_pagerduty_payload(payload)
    assert result["alert_name"] == "DB connection pool exhausted"
    assert result["severity"] == "high"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Generic webhook — response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_webhook_accepted():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.post(
            "/webhooks/generic",
            json={"alert_name": "High CPU on web-01", "severity": "high"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["alert_name"] == "High CPU on web-01"


@pytest.mark.asyncio
async def test_generic_webhook_no_alert_name():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.post("/webhooks/generic", json={})
    assert response.status_code == 200
    assert response.json()["alert_name"] == "Unknown alert"


# ---------------------------------------------------------------------------
# Generic webhook — DB persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_webhook_creates_incident_row():
    """POST /webhooks/generic must persist an Incident row to the DB."""
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.post(
            "/webhooks/generic",
            json={"alert_name": "Disk space low on db-01", "severity": "warning"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    incident_id = data.get("incident_id")
    assert incident_id is not None

    async with _TestSessionLocal() as session:
        incident: Incident | None = await session.get(Incident, incident_id)
    assert incident is not None
    assert incident.alert_name == "Disk space low on db-01"
    assert incident.source == "generic"
    assert incident.status == IncidentStatus.PENDING


@pytest.mark.asyncio
async def test_generic_webhook_stores_full_payload():
    """The alert_body column should hold the full original JSON payload."""
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.post(
            "/webhooks/generic",
            json={"alert_name": "Memory spike", "host": "app-03", "severity": "high"},
        )
    assert response.status_code == 200
    incident_id = response.json()["incident_id"]

    async with _TestSessionLocal() as session:
        incident: Incident | None = await session.get(Incident, incident_id)
    assert incident is not None
    assert incident.alert_body["host"] == "app-03"


# ---------------------------------------------------------------------------
# Approval endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_action_returns_200_and_approved_status():
    """POST /approvals/{id}/approve must return 200 and status=approved."""
    incident_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())

    async with _TestSessionLocal() as session:
        incident = Incident(
            id=incident_id,
            source="generic",
            alert_name="Test alert",
            alert_body={},
        )
        session.add(incident)
        approval = ApprovalRequest(
            id=approval_id,
            incident_id=incident_id,
            tool_name="restart_service",
            tool_input={"service": "nginx"},
            rationale="Service is down",
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        await session.commit()

    from runbookai.agent.harness import IncidentResult

    mock_result = IncidentResult(
        incident_id=incident_id,
        resolved=True,
        summary="Mocked resolution",
    )

    with patch(
        "runbookai.agent.harness.AgentHarness.resume_incident",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=_app()), base_url="http://test"
        ) as ac:
            response = await ac.post(f"/approvals/{approval_id}/approve")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["action_id"] == approval_id
    assert data["agent_result"]["resolved"] is True

    async with _TestSessionLocal() as session:
        updated: ApprovalRequest | None = await session.get(ApprovalRequest, approval_id)
    assert updated is not None
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.decided_at is not None


@pytest.mark.asyncio
async def test_approve_action_404_for_unknown_id():
    """Approving a non-existent action_id should return 404."""
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.post(f"/approvals/{uuid.uuid4()}/approve")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_approve_action_409_if_already_approved():
    """Approving an already-approved action should return 409."""
    incident_id = str(uuid.uuid4())
    approval_id = str(uuid.uuid4())

    async with _TestSessionLocal() as session:
        incident = Incident(
            id=incident_id,
            source="generic",
            alert_name="Duplicate approval test",
            alert_body={},
        )
        session.add(incident)
        approval = ApprovalRequest(
            id=approval_id,
            incident_id=incident_id,
            tool_name="restart_service",
            tool_input={"service": "redis"},
            rationale="Already approved",
            status=ApprovalStatus.APPROVED,
        )
        session.add(approval)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.post(f"/approvals/{approval_id}/approve")
    assert response.status_code == 409
