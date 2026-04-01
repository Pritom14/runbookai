"""Basic tests for webhook parsing."""

import pytest
from fastapi.testclient import TestClient

from runbookai.main import app
from runbookai.integrations.pagerduty import parse_pagerduty_payload

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generic_webhook_accepted():
    response = client.post(
        "/webhooks/generic",
        json={"alert_name": "High CPU on web-01", "severity": "high"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["alert_name"] == "High CPU on web-01"


def test_generic_webhook_no_alert_name():
    response = client.post("/webhooks/generic", json={})
    assert response.status_code == 200
    assert response.json()["alert_name"] == "Unknown alert"


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
