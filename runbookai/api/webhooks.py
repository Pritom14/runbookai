"""Webhook receivers — entry point for incoming alerts."""

import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import AsyncSessionLocal, get_session
from runbookai.integrations.pagerduty import parse_pagerduty_payload, verify_signature
from runbookai.models import AgentAction, Incident, IncidentStatus

logger = logging.getLogger("runbookai.api.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


_REMEDIATION_TOOLS = {"restart_service", "clear_disk", "scale_service"}
_REGRESSION_WINDOW_HOURS = 6


async def detect_regression(
    session: AsyncSession, service: str
) -> tuple[bool, str | None, str | None]:
    """Check if service had a recent remediation that may not have fixed the root cause.

    Returns (is_regression, prior_incident_id, prior_summary).
    """
    cutoff = datetime.utcnow() - timedelta(hours=_REGRESSION_WINDOW_HOURS)
    result = await session.execute(
        select(Incident)
        .where(
            Incident.created_at >= cutoff,
            Incident.status.in_([IncidentStatus.RESOLVED, IncidentStatus.ESCALATED]),
        )
        .order_by(Incident.created_at.desc())
        .limit(20)
    )
    recent = result.scalars().all()

    for prior in recent:
        body = prior.alert_body or {}
        prior_service = body.get("service", "")
        if prior_service != service:
            continue
        # Check if any remediation tool was used in this incident.
        actions_result = await session.execute(
            select(AgentAction).where(
                AgentAction.incident_id == prior.id,
                AgentAction.tool_name.in_(_REMEDIATION_TOOLS),
            )
        )
        remediation_actions = actions_result.scalars().all()
        if remediation_actions:
            return True, prior.id, prior.summary

    return False, None, None


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
    logger.info("PagerDuty alert received: %s id=%s", normalized["alert_name"], incident.id)
    return {
        "status": "accepted",
        "alert_name": normalized["alert_name"],
        "incident_id": incident.id,
    }


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
    service = payload.get("service", "")

    is_regression, prior_id, prior_summary = await detect_regression(session, service)
    if is_regression:
        logger.warning(
            "Regression detected: service=%s prior_incident=%s", service, prior_id
        )

    incident = Incident(
        id=str(uuid.uuid4()),
        source="generic",
        alert_name=alert_name,
        alert_body=payload,
        possible_regression=is_regression,
        prior_incident_id=prior_id,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    background_tasks.add_task(run_agent_for_incident, incident.id)
    logger.info("Generic alert received: %s incident_id=%s regression=%s",
                alert_name, incident.id, is_regression)
    return {
        "status": "accepted",
        "alert_name": alert_name,
        "incident_id": incident.id,
        "possible_regression": is_regression,
        "prior_incident_id": prior_id,
    }
