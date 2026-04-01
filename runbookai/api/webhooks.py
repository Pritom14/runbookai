"""Webhook receivers — entry point for incoming alerts."""

import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from runbookai.integrations.pagerduty import parse_pagerduty_payload, verify_signature

logger = logging.getLogger("runbookai.api.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/pagerduty")
async def pagerduty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_pagerduty_signature: str = Header(default=""),
):
    """Receive a PagerDuty v3 webhook, create an Incident, kick off the agent."""
    from runbookai.config import settings

    raw_body = await request.body()
    if not verify_signature(raw_body, x_pagerduty_signature, settings.pagerduty_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    normalized = parse_pagerduty_payload(payload)
    if not normalized:
        return {"status": "ignored"}

    # TODO: write Incident to DB, then background_tasks.add_task(run_agent, incident_id)
    logger.info("PagerDuty alert received: %s", normalized["alert_name"])
    return {"status": "accepted", "alert_name": normalized["alert_name"]}


@router.post("/generic")
async def generic_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive a generic alert payload.

    Expected body:
        {
            "alert_name": "High CPU on web-01",
            "description": "...",
            "host": "web-01",        # optional
            "severity": "high"       # optional
        }
    """
    payload = await request.json()
    alert_name = payload.get("alert_name", "Unknown alert")

    # TODO: write Incident to DB, then background_tasks.add_task(run_agent, incident_id)
    logger.info("Generic alert received: %s", alert_name)
    return {"status": "accepted", "alert_name": alert_name}
