"""Tests for postmortem endpoint and Slack notification module."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from runbookai.models import AgentAction, Base, Incident, IncidentStatus

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(_TEST_DB_URL, connect_args={"check_same_thread": False})
_TestSessionLocal = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _override_get_session():
    async with _TestSessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db():
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


async def _make_incident(
    *,
    status: str = IncidentStatus.RESOLVED,
    minutes_ago: int = 30,
    resolved: bool = True,
    possible_regression: bool = False,
    prior_incident_id: str | None = None,
    summary: str = "Agent signalled resolution.",
) -> Incident:
    inc_id = str(uuid.uuid4())
    created = datetime.utcnow() - timedelta(minutes=minutes_ago)
    resolved_at = created + timedelta(minutes=5) if resolved else None
    async with _TestSessionLocal() as session:
        inc = Incident(
            id=inc_id,
            source="generic",
            alert_name="checkout latency high",
            alert_body={"service": "checkout-service", "severity": "high"},
            status=status,
            created_at=created,
            resolved_at=resolved_at,
            summary=summary,
            possible_regression=possible_regression,
            prior_incident_id=prior_incident_id,
        )
        session.add(inc)
        await session.commit()
        await session.refresh(inc)
    return inc


async def _add_action(incident_id: str, tool_name: str, minutes_offset: int = 1) -> None:
    async with _TestSessionLocal() as session:
        action = AgentAction(
            id=str(uuid.uuid4()),
            incident_id=incident_id,
            tool_name=tool_name,
            tool_input={"host": "web-01"},
            tool_output={"status": "ok"},
            duration_ms=230,
            created_at=datetime.utcnow() - timedelta(minutes=minutes_offset),
        )
        session.add(action)
        await session.commit()


# ---------------------------------------------------------------------------
# Postmortem endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postmortem_404_for_unknown_incident():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{uuid.uuid4()}/postmortem")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_postmortem_returns_markdown():
    inc = await _make_incident()
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data
    assert "incident_id" in data
    assert "generated_at" in data
    assert data["incident_id"] == inc.id


@pytest.mark.asyncio
async def test_postmortem_markdown_contains_key_sections():
    inc = await _make_incident()
    await _add_action(inc.id, "check_logs")
    await _add_action(inc.id, "restart_service")

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    md = resp.json()["markdown"]

    assert "# Postmortem:" in md
    assert "## Metadata" in md
    assert "## Timeline" in md
    assert "## Actions Taken" in md
    assert "## Regression Analysis" in md
    assert "## Resolution" in md
    assert "## Recommendations" in md
    assert "_Generated automatically by RunbookAI" in md


@pytest.mark.asyncio
async def test_postmortem_includes_tool_actions():
    inc = await _make_incident()
    await _add_action(inc.id, "check_logs")
    await _add_action(inc.id, "run_db_check")

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    md = resp.json()["markdown"]

    assert "check_logs" in md
    assert "run_db_check" in md


@pytest.mark.asyncio
async def test_postmortem_regression_flag():
    prior_id = str(uuid.uuid4())
    inc = await _make_incident(possible_regression=True, prior_incident_id=prior_id)

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    md = resp.json()["markdown"]

    assert "Yes" in md
    assert prior_id in md


@pytest.mark.asyncio
async def test_postmortem_no_regression():
    inc = await _make_incident(possible_regression=False)

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    md = resp.json()["markdown"]

    assert "No regression detected" in md


@pytest.mark.asyncio
async def test_postmortem_empty_timeline():
    """Incident with no actions should still return valid markdown."""
    inc = await _make_incident()
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    assert resp.status_code == 200
    assert "## Timeline" in resp.json()["markdown"]


@pytest.mark.asyncio
async def test_postmortem_escalated_incident():
    inc = await _make_incident(
        status=IncidentStatus.ESCALATED,
        resolved=False,
        summary="Reached MAX_STEPS without resolving.",
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        resp = await ac.get(f"/incidents/{inc.id}/postmortem")
    md = resp.json()["markdown"]
    assert "escalated" in md.lower()


# ---------------------------------------------------------------------------
# Slack notification unit tests (no real HTTP calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_no_op_when_not_configured():
    """send_slack_notification must no-op silently when SLACK_WEBHOOK_URL is empty."""
    from runbookai.slack import send_slack_notification

    inc = await _make_incident()
    async with _TestSessionLocal() as session:
        incident = await session.get(Incident, inc.id)

    with patch("runbookai.slack.settings") as mock_settings:
        mock_settings.slack_webhook_url = ""
        # Should not raise and should not call httpx
        with patch("httpx.AsyncClient") as mock_client:
            await send_slack_notification("incident_started", incident)
            mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_slack_posts_for_incident_started():
    from runbookai.slack import send_slack_notification

    inc = await _make_incident()
    async with _TestSessionLocal() as session:
        incident = await session.get(Incident, inc.id)

    mock_response = AsyncMock()
    mock_response.status_code = 200

    with patch("runbookai.slack.settings") as mock_settings:
        mock_settings.slack_webhook_url = "https://hooks.slack.com/test"
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await send_slack_notification("incident_started", incident)

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            payload = call_kwargs[1]["json"]
            assert "blocks" in payload


@pytest.mark.asyncio
async def test_slack_never_raises_on_failure():
    """Slack failures must never propagate — agent loop must not break."""
    from runbookai.slack import send_slack_notification

    inc = await _make_incident()
    async with _TestSessionLocal() as session:
        incident = await session.get(Incident, inc.id)

    with patch("runbookai.slack.settings") as mock_settings:
        mock_settings.slack_webhook_url = "https://hooks.slack.com/test"
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=Exception("network down"))
            mock_client_cls.return_value = mock_client

            # Must not raise
            await send_slack_notification("incident_resolved", incident)


@pytest.mark.asyncio
async def test_slack_builds_blocks_for_all_events():
    """All event types must produce a valid blocks list."""
    from runbookai.slack import _build_blocks

    inc = await _make_incident()
    async with _TestSessionLocal() as session:
        incident = await session.get(Incident, inc.id)

    events = [
        ("incident_started", {}),
        ("approval_needed", {"tool": "restart_service", "rationale": "test", "approval_id": "abc"}),
        ("approval_granted", {"tool": "restart_service"}),
        ("approval_rejected", {"tool": "restart_service", "reason": "too risky"}),
        ("incident_resolved", {}),
        ("incident_escalated", {"reason": "max steps reached"}),
    ]
    for event, extra in events:
        blocks = _build_blocks(event, incident, extra)
        assert isinstance(blocks, list)
        assert len(blocks) >= 2
        # First block must be the header
        assert blocks[0]["type"] == "header"
