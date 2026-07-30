"""Focused demo/API contract tests for dashboard-facing endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from runbookai.models import AgentAction, Base, Incident, IncidentStatus

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_test_engine = create_async_engine(
    _TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)
_TestSessionLocal = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _override_get_session():
    async with _TestSessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db_and_override():
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


@pytest.mark.asyncio
async def test_bmc_sensors_match_chaos_board_contract():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        await ac.post("/bmc/control?action=reset")
        response = await ac.get("/bmc/sensors")

    assert response.status_code == 200
    body = response.json()

    for sensor_name in (
        "CPU_Temp",
        "Fan1_RPM",
        "Fan2_RPM",
        "PSU1_Voltage",
        "PSU1_Power",
    ):
        assert sensor_name in body["sensors"]
        assert isinstance(body["sensors"][sensor_name]["value"], (int, float))
        assert body["sensors"][sensor_name]["status"] in {"ok", "warning", "critical"}

    assert body["sensors"]["CPU_Temp"]["unit"] == "C"
    assert body["sensors"]["Fan1_RPM"]["unit"] == "RPM"
    assert body["sensors"]["Fan2_RPM"]["unit"] == "RPM"
    assert body["sensors"]["PSU1_Voltage"]["unit"] == "V"
    assert body["sensors"]["PSU1_Power"]["unit"] == "W"


@pytest.mark.asyncio
async def test_bmc_failure_stays_active_until_remediation():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        await ac.post("/bmc/control?action=thermal_failure")
        response = await ac.get("/bmc/sensors")

    body = response.json()
    assert body["mode"] == "thermal_failure"
    assert body["sensors"]["CPU_Temp"]["status"] == "critical"


@pytest.mark.asyncio
async def test_hardware_webhook_resolves_with_fan_override():
    from runbookai.api.webhooks import remediate_hardware_incident

    incident_id = str(uuid.uuid4())
    async with _TestSessionLocal() as session:
        async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
            await ac.post("/bmc/control?action=thermal_failure")

        incident = Incident(
            id=incident_id,
            source="hardware",
            alert_name="CPU Temperature",
            alert_body={"host": "web-01", "service": "rack-01", "severity": "critical"},
            status=IncidentStatus.IN_PROGRESS,
        )
        session.add(incident)
        await session.commit()

        await remediate_hardware_incident(session, incident)
        await session.refresh(incident)

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        replay_response = await ac.get(f"/incidents/{incident_id}/replay")

    assert incident.status == "resolved"
    assert incident.summary is not None
    assert "fan override" in incident.summary.lower()

    timeline = replay_response.json()["timeline"]
    assert [entry["tool_name"] for entry in timeline if entry["tool_name"]] == [
        "read_bmc_sensors",
        "fan_override",
    ]


@pytest.mark.asyncio
async def test_replay_timeline_matches_chaos_board_contract():
    incident_id = str(uuid.uuid4())
    started_at = datetime.utcnow()
    async with _TestSessionLocal() as session:
        session.add(
            Incident(
                id=incident_id,
                source="generic",
                alert_name="Hardware: CPU thermal alert",
                alert_body={"service": "demo-app", "severity": "critical"},
                status=IncidentStatus.RESOLVED,
                created_at=started_at,
                resolved_at=started_at + timedelta(seconds=12),
                summary="Fan override cooled the host.",
            )
        )
        session.add_all(
            [
                AgentAction(
                    incident_id=incident_id,
                    tool_name="_event",
                    tool_input={"event": "runbook_matched"},
                    tool_output={"alert_name": "Hardware: CPU thermal alert"},
                    duration_ms=0,
                    created_at=started_at,
                ),
                AgentAction(
                    incident_id=incident_id,
                    tool_name="read_bmc_sensors",
                    tool_input={"host": "localhost"},
                    tool_output={"CPU_Temp": 91.2, "Fan1_RPM": 2400, "status": "critical"},
                    duration_ms=42,
                    created_at=started_at + timedelta(seconds=2),
                ),
                AgentAction(
                    incident_id=incident_id,
                    tool_name="fan_override",
                    tool_input={"speed_percent": 100},
                    tool_output={"ok": True, "fan1_rpm": 4200},
                    duration_ms=55,
                    created_at=started_at + timedelta(seconds=4),
                ),
                AgentAction(
                    incident_id=incident_id,
                    tool_name="_event",
                    tool_input={"event": "resolved"},
                    tool_output={"incident_duration": 12},
                    duration_ms=0,
                    created_at=started_at + timedelta(seconds=12),
                ),
            ]
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as ac:
        response = await ac.get(f"/incidents/{incident_id}/replay")

    assert response.status_code == 200
    timeline = response.json()["timeline"]
    assert [entry["t_seconds"] for entry in timeline] == [0, 2, 4, 12]
    assert all("timestamp" in entry for entry in timeline)

    assert timeline[0]["event"] == "runbook_matched"
    assert timeline[0]["alert_name"] == "Hardware: CPU thermal alert"
    assert timeline[1]["tool_name"] == "read_bmc_sensors"
    assert timeline[1]["input"] == {"host": "localhost"}
    assert timeline[1]["output"]["CPU_Temp"] == 91.2
    assert timeline[1]["duration_ms"] == 42
    assert timeline[2]["tool_name"] == "fan_override"
    assert timeline[2]["input"]["speed_percent"] == 100
    assert timeline[2]["output"]["ok"] is True
    assert timeline[3]["event"] == "resolved"
    assert timeline[3]["incident_duration"] == 12
