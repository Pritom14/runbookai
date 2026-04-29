"""PagerDuty webhook integration.

Parses PagerDuty v3 webhook payloads into RunbookAI Incident objects.
Docs: https://developer.pagerduty.com/docs/webhooks/v3-overview/

Also provides functions to write back incident resolution to PagerDuty API.
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

import httpx

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


async def resolve_incident(
    incident_id: str,
    api_key: str,
    resolution_summary: str = "Resolved by RunbookAI",
) -> dict:
    """Resolve a PagerDuty incident via API.

    Args:
        incident_id: PagerDuty incident ID (e.g., "Q0RVJQLZWHSEKV")
        api_key: PagerDuty REST API token
        resolution_summary: Summary text for the resolution

    Returns:
        {
            "success": bool,
            "status": str,  # "ok" or "error"
            "message": str,
            "response": dict or None,  # full API response on success
        }

    Docs: https://developer.pagerduty.com/api-reference/reference/incidents/update-an-incident
    """
    if not api_key:
        logger.warning("PAGERDUTY_API_KEY not set — cannot write back incident resolution")
        return {
            "success": False,
            "status": "error",
            "message": "PagerDuty API key not configured",
        }

    url = f"https://api.pagerduty.com/incidents/{incident_id}"

    # PagerDuty API expects the incident to be updated via PUT with type and status
    update_payload = {
        "incidents": [
            {
                "id": incident_id,
                "type": "incident_reference",
                "status": "resolved",
            }
        ]
    }

    headers = {
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info("Writing back incident resolution to PagerDuty: %s", incident_id)
            response = await client.put(
                url,
                json=update_payload,
                headers=headers,
            )
            response.raise_for_status()
            result_data = response.json()

            logger.info(
                "PagerDuty incident %s resolved successfully (status=%s)",
                incident_id,
                response.status_code,
            )
            return {
                "success": True,
                "status": "ok",
                "message": f"Incident {incident_id} marked as resolved in PagerDuty",
                "response": result_data,
            }

    except httpx.HTTPStatusError as e:
        logger.error(
            "PagerDuty API error resolving %s: %d %s",
            incident_id,
            e.response.status_code,
            e.response.text[:500],
        )
        return {
            "success": False,
            "status": "error",
            "message": f"PagerDuty API error: {e.response.status_code} {e.response.reason_phrase}",
        }
    except Exception as e:
        logger.error("Failed to resolve PagerDuty incident %s: %s", incident_id, e)
        return {
            "success": False,
            "status": "error",
            "message": f"Connection error: {str(e)}",
        }
