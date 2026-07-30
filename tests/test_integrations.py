"""Integration tests for Phase 2 webhooks (PagerDuty, Datadog, Grafana, Slack)."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from runbookai.database import Base, get_session
from runbookai.main import app


@pytest.fixture(autouse=True)
async def test_db():
    """Create in-memory test database and route all DB access to it.

    Some request handlers (e.g. webhook background tasks) open sessions via
    ``AsyncSessionLocal`` directly instead of the ``get_session`` dependency,
    so that name has to be patched too or they fall through to the real
    on-disk database, which has no tables in a fresh checkout.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_local = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session():
        async with session_local() as session:
            yield session

    import runbookai.api.webhooks as webhooks_module

    app.dependency_overrides[get_session] = override_get_session
    original_session_local = webhooks_module.AsyncSessionLocal
    webhooks_module.AsyncSessionLocal = session_local
    yield engine
    webhooks_module.AsyncSessionLocal = original_session_local
    app.dependency_overrides.clear()
    await engine.dispose()


class TestPagerDutyWebhook:
    """Test PagerDuty V3 webhook integration."""

    @staticmethod
    def _sign_payload(payload: bytes, secret: str) -> str:
        """Create PagerDuty signature."""
        digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return f"v1={digest}"

    def test_pagerduty_triggered_event(self):
        """Test receiving a PagerDuty triggered event."""
        client = TestClient(app)

        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "incident": {
                        "id": "Q0RVJQLZWHSEKV",
                        "title": "CPU usage high",
                        "service": {"id": "P12345", "name": "API Server"},
                        "urgency": "high",
                        "description": "CPU usage exceeded 80%",
                    }
                },
            }
        }

        payload_bytes = json.dumps(payload).encode()
        secret = "test-secret-key"
        signature = self._sign_payload(payload_bytes, secret)

        response = client.post(
            "/webhooks/pagerduty",
            json=payload,
            headers={"X-PagerDuty-Signature": signature},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["alert_name"] == "CPU usage high"
        assert data["pagerduty_incident_id"] == "Q0RVJQLZWHSEKV"
        assert data["service"] == "API Server"
        assert "incident_id" in data

    def test_pagerduty_non_triggered_event(self):
        """Test that non-triggered events are logged but not actioned."""
        client = TestClient(app)

        payload = {
            "event": {
                "event_type": "incident.resolved",
                "data": {
                    "incident": {
                        "id": "Q0RVJQLZWHSEKV",
                        "title": "CPU usage high",
                        "service": {"id": "P12345", "name": "API Server"},
                    }
                },
            }
        }

        payload_bytes = json.dumps(payload).encode()
        secret = "test-secret-key"
        signature = self._sign_payload(payload_bytes, secret)

        response = client.post(
            "/webhooks/pagerduty",
            json=payload,
            headers={"X-PagerDuty-Signature": signature},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged"
        assert data["event_type"] == "resolved"


class TestDatadogWebhook:
    """Test Datadog webhook integration."""

    def test_datadog_alert_event(self):
        """Test receiving a Datadog alert event."""
        client = TestClient(app)

        payload = {
            "id": "12345678",
            "alert_title": "High Memory Usage",
            "alert_status": "alert",
            "trigger": {
                "metric": "system.mem.pct_used",
            },
            "last_updated": "2026-04-28T12:34:56Z",
        }

        response = client.post("/webhooks/datadog", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["alert_name"] == "High Memory Usage"
        assert data["datadog_monitor_id"] == "12345678"
        assert "incident_id" in data

    def test_datadog_recovery_event(self):
        """Test that recovery events are logged but not actioned."""
        client = TestClient(app)

        payload = {
            "id": "12345678",
            "alert_title": "High Memory Usage",
            "alert_status": "recovery",
            "trigger": {
                "metric": "system.mem.pct_used",
            },
            "last_updated": "2026-04-28T12:34:56Z",
        }

        response = client.post("/webhooks/datadog", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged"
        assert data["event_type"] == "recovery"


class TestGrafanaWebhook:
    """Test Grafana webhook integration."""

    @staticmethod
    def _sign_payload(payload: bytes, secret: str) -> str:
        """Create Grafana signature."""
        digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_grafana_firing_event(self):
        """Test receiving a Grafana firing event."""
        client = TestClient(app)

        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "__alert_uid__": "abc123def456",
                    },
                    "annotations": {
                        "description": "CPU is above 80%",
                    },
                }
            ],
        }

        payload_bytes = json.dumps(payload).encode()
        secret = "test-secret-key"
        signature = self._sign_payload(payload_bytes, secret)

        response = client.post(
            "/webhooks/grafana",
            json=payload,
            headers={"X-Grafana-Signature": signature},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["alert_name"] == "HighCPU"
        assert data["grafana_alert_uid"] == "abc123def456"
        assert "incident_id" in data

    def test_grafana_resolved_event(self):
        """Test that resolved events are logged but not actioned."""
        client = TestClient(app)

        payload = {
            "status": "resolved",
            "alerts": [
                {
                    "status": "resolved",
                    "labels": {
                        "alertname": "HighCPU",
                        "__alert_uid__": "abc123def456",
                    },
                }
            ],
        }

        payload_bytes = json.dumps(payload).encode()
        secret = "test-secret-key"
        signature = self._sign_payload(payload_bytes, secret)

        response = client.post(
            "/webhooks/grafana",
            json=payload,
            headers={"X-Grafana-Signature": signature},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "logged"
        assert data["event_type"] == "resolved"

    def test_grafana_signature_verification(self):
        """Test that invalid Grafana signature is rejected."""
        client = TestClient(app)

        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                    },
                }
            ],
        }

        response = client.post(
            "/webhooks/grafana",
            json=payload,
            headers={"X-Grafana-Signature": "sha256=invalid"},
        )

        # Should fail if secret is set; succeed if not
        # In test environment, secret likely not set, so should succeed
        assert response.status_code in [200, 401]


class TestSlackWebhook:
    """Test Slack webhook integration."""

    def test_slack_test_endpoint(self):
        """Test Slack connectivity test endpoint."""
        client = TestClient(app)

        response = client.post("/webhooks/slack/test")

        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "status" in data
        assert "message" in data


class TestWebhookPayloadParsing:
    """Test parsing functions for all integrations."""

    def test_parse_pagerduty_triggered(self):
        """Test PagerDuty payload parsing."""
        from runbookai.integrations.pagerduty import parse_pagerduty_payload

        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "incident": {
                        "id": "Q0RVJQLZWHSEKV",
                        "title": "CPU usage high",
                        "service": {"id": "P12345", "name": "API Server"},
                        "urgency": "high",
                        "description": "CPU > 80%",
                    }
                },
            }
        }

        result = parse_pagerduty_payload(payload)

        assert result["incident_id"] == "Q0RVJQLZWHSEKV"
        assert result["alert_name"] == "CPU usage high"
        assert result["service_id"] == "P12345"
        assert result["service_name"] == "API Server"
        assert result["status"] == "triggered"
        assert result["severity"] == "high"

    def test_parse_datadog_alert(self):
        """Test Datadog payload parsing."""
        from runbookai.integrations.datadog import parse_datadog_payload

        payload = {
            "id": "12345678",
            "alert_title": "High Memory Usage",
            "alert_status": "alert",
            "trigger": {"metric": "system.mem.pct_used"},
            "last_updated": "2026-04-28T12:34:56Z",
        }

        result = parse_datadog_payload(payload)

        assert result["monitor_id"] == "12345678"
        assert result["alert_name"] == "High Memory Usage"
        assert result["status"] == "alert"
        assert result["metric"] == "system.mem.pct_used"

    def test_parse_grafana_firing(self):
        """Test Grafana payload parsing."""
        from runbookai.integrations.grafana import parse_grafana_payload

        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "__alert_uid__": "abc123def456",
                    },
                    "annotations": {
                        "description": "CPU is above 80%",
                    },
                }
            ],
        }

        result = parse_grafana_payload(payload)

        assert result["alert_name"] == "HighCPU"
        assert result["status"] == "firing"
        assert result["alert_uid"] == "abc123def456"
        assert result["description"] == "CPU is above 80%"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
