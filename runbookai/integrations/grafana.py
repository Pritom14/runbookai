"""Grafana webhook integration.

Parses Grafana alert webhook payloads and verifies HMAC-SHA256 signature.
Posts resolution updates back to Grafana API.

Docs: https://grafana.com/docs/grafana/latest/alerting/manage-alerts/manage-state-and-history/
"""

import hashlib
import hmac
import logging

import httpx

logger = logging.getLogger("runbookai.integrations.grafana")


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """Verify Grafana webhook HMAC-SHA256 signature.

    Grafana sends 'X-Grafana-Signature' header with format: sha256=<hex_digest>

    Args:
        payload: Raw request body bytes
        signature_header: Value of X-Grafana-Signature header
        secret: Grafana webhook secret (from webhook configuration)

    Returns:
        True if signature is valid or no secret is configured, False otherwise.
    """
    if not secret:
        logger.warning("No GRAFANA_WEBHOOK_SECRET set — skipping signature verification")
        return True

    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def parse_grafana_payload(payload: dict) -> dict:
    """Parse a Grafana alert webhook payload into a normalized incident dict.

    Grafana sends webhooks with structure:
    {
        "status": "firing" or "resolved",
        "alerts": [
            {
                "status": "firing" or "resolved",
                "labels": {
                    "alertname": "...",
                    ...
                },
                "annotations": {
                    "description": "...",
                    ...
                },
                ...
            }
        ],
        "groupLabels": {...},
        "commonLabels": {...},
        ...
    }

    Returns:
        {
            "alert_name": str,       # alert name from labels
            "status": str,           # "firing" or "resolved"
            "alert_uid": str,        # unique alert identifier
            "description": str,      # annotation description
            "raw": dict,             # full original payload
        }

    Returns empty dict if format is unexpected or no alerts present.
    """
    status = payload.get("status", "")
    alerts = payload.get("alerts", [])

    if not alerts:
        logger.warning("Grafana webhook has no alerts")
        return {}

    # Take the first alert (typically only one per webhook)
    alert = alerts[0]
    alert_status = alert.get("status", status)
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    normalized = {
        "alert_name": labels.get("alertname", "Grafana Alert"),
        "status": alert_status,  # "firing" or "resolved"
        "alert_uid": alert.get("labels", {}).get("__alert_uid__", ""),
        "description": annotations.get("description", ""),
        "raw": payload,
    }

    logger.info(
        "Parsed Grafana webhook: status=%s alertname=%s uid=%s",
        alert_status,
        normalized["alert_name"],
        normalized.get("alert_uid", "?")[:16],
    )

    return normalized


async def close_alert(
    alert_uid: str,
    api_key: str,
    base_url: str,
    reason: str = "Resolved by RunbookAI",
) -> dict:
    """Close/resolve a Grafana alert via API.

    Args:
        alert_uid: Grafana alert UID
        api_key: Grafana API key
        base_url: Grafana base URL (e.g., "https://grafana.example.com")
        reason: Reason for closing the alert

    Returns:
        {
            "success": bool,
            "status": str,
            "message": str,
            "response": dict or None,
        }

    Note: Grafana handles alert state through provisioning API.
    For now we log the closure and update the rule if needed.

    Docs: https://grafana.com/docs/grafana/latest/developers/http_api/
    """
    if not api_key:
        logger.warning("GRAFANA_API_KEY not set — cannot close alert")
        return {
            "success": False,
            "status": "error",
            "message": "Grafana API key not configured",
        }

    if not base_url:
        logger.warning("GRAFANA_BASE_URL not set — cannot close alert")
        return {
            "success": False,
            "status": "error",
            "message": "Grafana base URL not configured",
        }

    # Ensure base_url has no trailing slash
    base_url = base_url.rstrip("/")

    # For now, we can only log the closure since Grafana handles state
    # through alert rules, not a direct API to close alerts.
    # In a real scenario, you might update the rule state or create an annotation.
    url = f"{base_url}/api/annotations"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "text": f"RunbookAI: {reason}",
        "tags": ["runbookai", "resolved"],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info(
                "Grafana API call: POST %s (alert_uid=%s reason=%s)",
                url,
                alert_uid[:16],
                reason[:60],
            )
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            result_data = response.json()

            logger.info(
                "Grafana API success: alert logged (status=%d)",
                response.status_code,
            )
            return {
                "success": True,
                "status": "ok",
                "message": f"Alert closure logged in Grafana for alert {alert_uid[:16]}",
                "response": result_data,
            }

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text[:500]
        logger.error(
            "Grafana API error: POST %s failed with %d — %s",
            url,
            e.response.status_code,
            error_detail,
        )
        return {
            "success": False,
            "status": "error",
            "message": f"Grafana API error: {e.response.status_code}",
            "error_detail": error_detail,
        }
    except Exception as e:
        logger.error("Grafana API connection error: %s", str(e))
        return {
            "success": False,
            "status": "error",
            "message": f"Connection error: {str(e)}",
        }
