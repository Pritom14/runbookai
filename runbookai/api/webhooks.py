"""Webhook receivers — entry point for incoming alerts."""

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import AsyncSessionLocal, get_session
from runbookai.integrations.pagerduty import parse_pagerduty_payload, verify_signature
from runbookai.models import Incident

logger = logging.getLogger("runbookai.api.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def run_agent_for_incident(incident_id: str) -> None:
    """Background task: spin up an AgentHarness and run it for the given incident."""
    from runbookai.agent.harness import AgentHarness

    async with AsyncSessionLocal() as session:
        harness = AgentHarness(incident_id=incident_id)
        try:
            result = await harness.run(session)
            logger.info(
                "incident=%s background run finished resolved=%s",
                incident_id,
                result.resolved,
            )
        except Exception:
            logger.exception("incident=%s background agent raised", incident_id)


@router.post("/pagerduty")
async def pagerduty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
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

    incident = Incident(
        id=str(uuid.uuid4()),
        source="pagerduty",
        alert_name=normalized["alert_name"],
        alert_body=payload,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    background_tasks.add_task(run_agent_for_incident, incident.id)
    logger.info("PagerDuty alert received: %s incident_id=%s", normalized["alert_name"], incident.id)
    return {"status": "accepted", "alert_name": normalized["alert_name"], "incident_id": incident.id}


@router.post("/generic")
async def generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
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

    incident = Incident(
        id=str(uuid.uuid4()),
        source="generic",
        alert_name=alert_name,
        alert_body=payload,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    background_tasks.add_task(run_agent_for_incident, incident.id)
    logger.info("Generic alert received: %s incident_id=%s", alert_name, incident.id)
    return {"status": "accepted", "alert_name": alert_name, "incident_id": incident.id}
