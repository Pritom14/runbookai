"""PagerDuty webhook integration.

Parses PagerDuty v3 webhook payloads into RunbookAI Incident objects.
Docs: https://developer.pagerduty.com/docs/webhooks/v3-overview/
"""

import hashlib
import hmac
import logging

logger = logging.getLogger("runbookai.integrations.pagerduty")


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """Verify PagerDuty webhook HMAC-SHA256 signature.

    TODO: PagerDuty sends 'X-PagerDuty-Signature' header.
    Format: v1=<hex_digest>
    """
    if not secret:
        logger.warning("No PAGERDUTY_WEBHOOK_SECRET set — skipping signature verification")
        return True

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("v1=")
    return hmac.compare_digest(expected, provided)


def parse_pagerduty_payload(payload: dict) -> dict:
    """Parse a PagerDuty v3 webhook payload into a normalized incident dict.

    Returns:
        {
            "alert_name": str,
            "service": str,
            "severity": str,
            "description": str,
            "raw": dict,  # full original payload
        }

    TODO: Handle all event types (trigger, acknowledge, resolve, reassign).
    Currently only handles "incident.triggered".
    """
    event = payload.get("event", {})
    event_type = event.get("event_type", "")
    data = event.get("data", {})

    if event_type != "incident.triggered":
        logger.info("Ignoring PagerDuty event type: %s", event_type)
        return {}

    incident_data = data.get("incident", data)
    return {
        "alert_name": incident_data.get("title", "Unknown alert"),
        "service": incident_data.get("service", {}).get("name", ""),
        "severity": incident_data.get("urgency", "high"),
        "description": incident_data.get("description", ""),
        "raw": payload,
    }
