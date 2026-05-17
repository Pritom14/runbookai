"""Datadog webhook integration.

Parses Datadog monitor webhook payloads and maps to RunbookAI incidents.
Posts resolution events back to Datadog Events API.

Docs: https://docs.datadoghq.com/integrations/webhooks/
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger("runbookai.integrations.datadog")


def parse_datadog_payload(payload: dict) -> dict:
    """Parse a Datadog monitor webhook payload into a normalized incident dict.

    Datadog sends webhooks with structure:
    {
        "alert": {...},
        "trigger": {"title": "...", "metric": "...", ...},
        ...
    }

    Returns:
        {
            "alert_name": str,      # monitor title
            "status": str,          # "alert" or "recovery"
            "monitor_id": str,      # Datadog monitor ID
            "metric": str,          # metric name
            "last_updated": str,    # timestamp
            "raw": dict,            # full original payload
        }

    Returns empty dict if payload format is unexpected.
    """
    trigger = payload.get("trigger", {})

    monitor_id = str(payload.get("id", ""))
    alert_name = payload.get("alert_title", "Datadog Alert")
    status = payload.get("alert_status", "alert")
    metric = trigger.get("metric", "")
    last_updated = payload.get("last_updated", "")

    normalized = {
        "alert_name": alert_name,
        "status": status,  # "alert" or "recovery"
        "monitor_id": monitor_id,
        "metric": metric,
        "last_updated": last_updated,
        "raw": payload,
    }

    logger.info(
        "Parsed Datadog webhook: monitor=%s status=%s title=%s",
        monitor_id,
        status,
        alert_name,
    )

    return normalized


async def post_event(
    event_title: str,
    api_key: str,
    site: str = "datadoghq.com",
    alert_type: str = "success",
    text: str = "",
    tags: Optional[list] = None,
) -> dict:
    """Post an event to Datadog Events API.

    Args:
        event_title: Title of the event (required)
        api_key: Datadog API key
        site: Datadog site ("datadoghq.com", "us3.datadoghq.com", etc.)
        alert_type: "info", "warning", "error", or "success"
        text: Event description
        tags: List of tags to attach to the event

    Returns:
        {
            "success": bool,
            "status": str,
            "message": str,
            "response": dict or None,
        }

    Docs: https://docs.datadoghq.com/api/latest/events/
    """
    if not api_key:
        logger.warning("DATADOG_API_KEY not set — cannot post event")
        return {
            "success": False,
            "status": "error",
            "message": "Datadog API key not configured",
        }

    url = f"https://api.{site}/api/v1/events"

    headers = {
        "DD-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "title": event_title,
        "text": text,
        "alert_type": alert_type,
        "host": "runbookai",
    }
    if tags:
        payload["tags"] = tags

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info(
                "Datadog API call: POST %s (title=%s alert_type=%s)",
                url,
                event_title[:60],
                alert_type,
            )
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            result_data = response.json()

            logger.info(
                "Datadog API success: event posted (status=%d)",
                response.status_code,
            )
            return {
                "success": True,
                "status": "ok",
                "message": f"Event '{event_title}' posted to Datadog",
                "response": result_data,
            }

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text[:500]
        logger.error(
            "Datadog API error: POST %s failed with %d — %s",
            url,
            e.response.status_code,
            error_detail,
        )
        return {
            "success": False,
            "status": "error",
            "message": f"Datadog API error: {e.response.status_code}",
            "error_detail": error_detail,
        }
    except Exception as e:
        logger.error("Datadog API connection error: %s", str(e))
        return {
            "success": False,
            "status": "error",
            "message": f"Connection error: {str(e)}",
        }
