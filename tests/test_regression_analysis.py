"""Tests for regression detection and cross-incident analysis endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from runbookai.models import AgentAction, Base, Incident, IncidentStatus

# ---------------------------------------------------------------------------
# In-memory SQLite DB fixture (same pattern as test_webhooks.py)
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(_TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSessionLocal = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _override_get_session():
    async with _TestSessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db_and_override():
    # Drop and recreate tables to isolate each test.
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from runbookai.database import get_session
    from runbookai.main import app

    app.dependency_overrides[get_session] = _override_get_session
    yield
    app.dependency_overrides.clear()


def _app():
    from runbookai.main import app
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_incident(
    service: str,
    status: str,
    *,
    minutes_ago: int = 30,
    possible_regression: bool = False,
    prior_incident_id: str | None = None,
    summary: str | None = None,
    resolved: bool = False,
) -> Incident:
    inc_id = str(uuid.uuid4())
    created = datetime.utcnow() - timedelta(minutes=minutes_ago)
    resolved_at = created + timedelta(minutes=5) if resolved else None
    async with _TestSessionLocal() as session:
        inc = Incident(
            id=inc_id,
            source="generic",
            alert_name=f"{service} alert",
            alert_body={"service": service},
            status=status,
            created_at=created,
            resolved_at=resolved_at,
            possible_regression=possible_regression,
            prior_incident_id=prior_incident_id,
            summary=summary,
        )
        session.add(inc)
        await session.commit()
        await session.refresh(inc)
    return inc


async def _seed_action(incident_id: str, tool_name: str) -> None:
    async with _TestSessionLocal() as session:
        action = AgentAction(
            id=str(uuid.uuid4()),
            incident_id=incident_id,
            tool_name=tool_name,
            tool_input={"service": "svc"},
            tool_output={"status": "ok"},
        )
        session.add(action)
        await session.commit()


# ---------------------------------------------------------------------------
# detect_regression unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_regression_no_prior_incidents():
    from runbookai.api.webhooks import detect_regression

    async with _TestSessionLocal() as session:
        is_reg, prior_id, prior_summary = await detect_regression(session, "checkout-service")
    assert is_reg is False
    assert prior_id is None


@pytest.mark.asyncio
async def test_detect_regression_prior_with_remediation():
    """A prior resolved incident that used restart_service should flag regression."""
    from runbookai.api.webhooks import detect_regression

    prior = await _seed_incident("checkout-service", IncidentStatus.RESOLVED, minutes_ago=60)
    await _seed_action(prior.id, "restart_service")

    async with _TestSessionLocal() as session:
        is_reg, prior_id, _ = await detect_regression(session, "checkout-service")
    assert is_reg is True
    assert prior_id == prior.id


@pytest.mark.asyncio
async def test_detect_regression_different_service_not_flagged():
    """Remediation on a different service must NOT trigger regression for our service."""
    from runbookai.api.webhooks import detect_regression

    prior = await _seed_incident("payment-service", IncidentStatus.RESOLVED, minutes_ago=30)
    await _seed_action(prior.id, "restart_service")

    async with _TestSessionLocal() as session:
        is_reg, _, _ = await detect_regression(session, "checkout-service")
    assert is_reg is False


@pytest.mark.asyncio
async def test_detect_regression_only_diagnostic_tools_not_flagged():
    """A prior incident that only ran check_logs (no remediation) must NOT flag regression."""
    from runbookai.api.webhooks import detect_regression

    prior = await _seed_incident("checkout-service", IncidentStatus.RESOLVED, minutes_ago=45)
    await _seed_action(prior.id, "check_logs")
    await _seed_action(prior.id, "http_check")

    async with _TestSessionLocal() as session:
        is_reg, _, _ = await detect_regression(session, "checkout-service")
    assert is_reg is False


@pytest.mark.asyncio
async def test_detect_regression_outside_window_not_flagged():
    """Incidents older than the 6-hour window must not trigger regression."""
    from runbookai.api.webhooks import detect_regression

    prior = await _seed_incident(
        "checkout-service", IncidentStatus.RESOLVED, minutes_ago=400
    )
    await _seed_action(prior.id, "restart_service")

    async with _TestSessionLocal() as session:
        is_reg, _, _ = await detect_regression(session, "checkout-service")
    assert is_reg is False


@pytest.mark.asyncio
async def test_generic_webhook_sets_regression_flag():
    """When second alert for same service fires, response includes possible_regression=True."""
    # Seed a prior resolved incident with remediation
    prior = await _seed_incident("web-app", IncidentStatus.RESOLVED, minutes_ago=120)
    await _seed_action(prior.id, "restart_service")

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.post(
            "/webhooks/generic",
            json={"alert_name": "web-app latency high", "service": "web-app"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["possible_regression"] is True
    assert data["prior_incident_id"] == prior.id


# ---------------------------------------------------------------------------
# /incidents/analysis endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analysis_empty_window():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get("/incidents/analysis?hours=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_incidents"] == 0
    assert data["by_service"] == []


@pytest.mark.asyncio
async def test_analysis_counts_by_service():
    """Analysis should group incidents by service and compute totals."""
    await _seed_incident("api-gw", IncidentStatus.RESOLVED, minutes_ago=10, resolved=True)
    await _seed_incident("api-gw", IncidentStatus.ESCALATED, minutes_ago=20)
    await _seed_incident("db", IncidentStatus.RESOLVED, minutes_ago=30, resolved=True)

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get("/incidents/analysis?hours=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_incidents"] == 3
    assert data["resolved"] == 2

    by_service = {s["service"]: s for s in data["by_service"]}
    assert "api-gw" in by_service
    assert by_service["api-gw"]["total_incidents"] == 2
    assert by_service["api-gw"]["escalated"] == 1
    assert by_service["db"]["total_incidents"] == 1


@pytest.mark.asyncio
async def test_analysis_regression_count():
    """Regression incidents must be counted in regressions_detected."""
    prior = await _seed_incident("svc", IncidentStatus.RESOLVED, minutes_ago=50, resolved=True)
    await _seed_incident(
        "svc",
        IncidentStatus.ESCALATED,
        minutes_ago=10,
        possible_regression=True,
        prior_incident_id=prior.id,
    )

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get("/incidents/analysis?hours=2")
    data = resp.json()
    assert data["regressions_detected"] >= 1


@pytest.mark.asyncio
async def test_analysis_auto_resolution_rate():
    await _seed_incident("svc", IncidentStatus.RESOLVED, minutes_ago=5, resolved=True)
    await _seed_incident("svc", IncidentStatus.RESOLVED, minutes_ago=10, resolved=True)
    await _seed_incident("svc", IncidentStatus.ESCALATED, minutes_ago=15)

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get("/incidents/analysis?hours=1")
    data = resp.json()
    assert data["total_incidents"] == 3
    assert data["resolved"] == 2
    assert data["auto_resolution_rate_pct"] == pytest.approx(66.7, abs=0.2)


# ---------------------------------------------------------------------------
# /incidents/compare endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_incidents_not_found():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(
            f"/incidents/compare?incident_a={uuid.uuid4()}&incident_b={uuid.uuid4()}"
        )
    assert resp.status_code == 200
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_compare_incidents_basic():
    """Compare two real incidents — response must include diff keys."""
    inc_a = await _seed_incident("svc", IncidentStatus.RESOLVED, minutes_ago=90, resolved=True)
    inc_b = await _seed_incident(
        "svc",
        IncidentStatus.ESCALATED,
        minutes_ago=10,
        possible_regression=True,
        prior_incident_id=inc_a.id,
    )
    await _seed_action(inc_a.id, "check_logs")
    await _seed_action(inc_a.id, "restart_service")
    await _seed_action(inc_b.id, "check_logs")
    await _seed_action(inc_b.id, "run_db_check")

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(
            f"/incidents/compare?incident_a={inc_a.id}&incident_b={inc_b.id}"
        )
    assert resp.status_code == 200
    data = resp.json()

    assert "incident_a" in data
    assert "incident_b" in data
    assert "diff" in data

    diff = data["diff"]
    assert "tools_added_in_b" in diff
    assert "tools_dropped_in_b" in diff
    assert "gap_minutes" in diff
    assert diff["is_regression"] is True

    # restart_service was in A but not B
    assert "restart_service" in diff["tools_dropped_in_b"]
    # run_db_check was in B but not A
    assert "run_db_check" in diff["tools_added_in_b"]


@pytest.mark.asyncio
async def test_compare_gap_minutes():
    """Gap should be approximately the time difference between incidents."""
    inc_a = await _seed_incident("svc", IncidentStatus.RESOLVED, minutes_ago=70, resolved=True)
    inc_b = await _seed_incident("svc", IncidentStatus.ESCALATED, minutes_ago=10)

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(
            f"/incidents/compare?incident_a={inc_a.id}&incident_b={inc_b.id}"
        )
    data = resp.json()
    gap = data["diff"]["gap_minutes"]
    # Expect roughly 60 minutes (70-10), allow ±2 for timing
    assert 55 <= gap <= 65
