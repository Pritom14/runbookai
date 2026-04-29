"""Webhook receivers — entry point for incoming alerts."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import AsyncSessionLocal, get_session
from runbookai.integrations.pagerduty import parse_pagerduty_payload, verify_signature as verify_pagerduty_signature
from runbookai.integrations.datadog import parse_datadog_payload
from runbookai.integrations.grafana import parse_grafana_payload, verify_signature as verify_grafana_signature
from runbookai.integrations.slack_integration import test_webhook as test_slack_webhook
from runbookai.models import AgentAction, Incident, IncidentStatus

logger = logging.getLogger("runbookai.api.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


_REMEDIATION_TOOLS = {"restart_service", "clear_disk", "scale_service"}
_REGRESSION_WINDOW_HOURS = 6


async def detect_regression(
    session: AsyncSession, service: str
) -> Tuple[bool, Optional[str], Optional[str]]:
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
    """Receive a PagerDuty v3 webhook, create an Incident, kick off the agent.

    Supports event types: incident.triggered, incident.acknowledged,
    incident.resolved, incident.reassigned.

    For triggered events: creates a new RunbookAI incident and starts the agent.
    For other event types: logs but doesn't create incident (handled by PagerDuty callbacks).
    """
    from runbookai.config import settings

    raw_body = await request.body()
    if not verify_pagerduty_signature(raw_body, x_pagerduty_signature, settings.pagerduty_webhook_secret):
        logger.warning("PagerDuty webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    normalized = parse_pagerduty_payload(payload)
    if not normalized:
        return {"status": "ignored"}

    # Only create incidents for triggered events; other events are informational
    event_type = normalized.get("status", "")
    if event_type != "triggered":
        logger.info(
            "PagerDuty event %s for incident %s (service=%s) — logged but not actioned",
            event_type,
            normalized.get("incident_id", "unknown"),
            normalized.get("service_name", "unknown"),
        )
        return {
            "status": "logged",
            "event_type": event_type,
            "incident_id": normalized.get("incident_id", ""),
            "message": f"Event type '{event_type}' logged but not actionable",
        }

    # Create incident for triggered events
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
    logger.info(
        "PagerDuty incident triggered: %s (pd_id=%s service=%s runbookai_id=%s)",
        normalized["alert_name"],
        normalized.get("incident_id", "?"),
        normalized.get("service_name", "?"),
        incident.id,
    )
    return {
        "status": "accepted",
        "alert_name": normalized["alert_name"],
        "incident_id": incident.id,
        "pagerduty_incident_id": normalized.get("incident_id", ""),
        "service": normalized.get("service_name", ""),
    }


class GenericWebhookPayload(BaseModel):
    alert_name: str
    description: str = ""
    host: str = ""
    service: str = ""
    severity: str = "unknown"


@router.post("/generic")
async def generic_webhook(
    payload: GenericWebhookPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Receive a generic alert payload.

    Required fields:
        alert_name (str): Human-readable name of the alert.

    Optional fields:
        description, host, service, severity.

    Returns 422 if required fields are missing.
    """
    alert_name = payload.alert_name
    service = payload.service

    is_regression, prior_id, prior_summary = await detect_regression(session, service)
    if is_regression:
        logger.warning(
            "Regression detected: service=%s prior_incident=%s", service, prior_id
        )

    incident = Incident(
        id=str(uuid.uuid4()),
        source="generic",
        alert_name=alert_name,
        alert_body=payload.model_dump(),
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

@router.post("/hardware")
async def hardware_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    payload = await request.json()
    alert_name = payload.get("title", payload.get("alert_name", "Unknown hardware alert"))
    service = payload.get("service", "")
    is_regression, prior_id, prior_summary = await detect_regression(session, service)
    incident = Incident(
        id=str(uuid.uuid4()),
        source="hardware",
        alert_name=alert_name,
        alert_body=payload,
        possible_regression=is_regression,
        prior_incident_id=prior_id,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)
    background_tasks.add_task(run_agent_for_incident, incident.id)
    logger.info("Hardware alert received: %s incident_id=%s", alert_name, incident.id)
    return {
        "status": "accepted",
        "alert_name": alert_name,
        "incident_id": incident.id,
        "possible_regression": is_regression,
        "prior_incident_id": prior_id,
    }


@router.post("/datadog")
async def datadog_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Receive a Datadog monitor webhook, create an Incident, kick off the agent.

    Only processes "alert" status (not "recovery" which is handled by callbacks).
    """
    payload = await request.json()
    normalized = parse_datadog_payload(payload)
    if not normalized:
        return {"status": "ignored"}

    # Only create incidents for alert status; recovery is logged but not actioned
    status = normalized.get("status", "")
    if status != "alert":
        logger.info(
            "Datadog webhook %s for monitor %s — logged but not actioned",
            status,
            normalized.get("monitor_id", "unknown"),
        )
        return {
            "status": "logged",
            "event_type": status,
            "monitor_id": normalized.get("monitor_id", ""),
            "message": f"Status '{status}' logged but not actionable",
        }

    # Create incident for alert status
    incident = Incident(
        id=str(uuid.uuid4()),
        source="datadog",
        alert_name=normalized["alert_name"],
        alert_body=payload,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    background_tasks.add_task(run_agent_for_incident, incident.id)
    logger.info(
        "Datadog alert received: %s (monitor=%s runbookai_id=%s)",
        normalized["alert_name"],
        normalized.get("monitor_id", "?"),
        incident.id,
    )
    return {
        "status": "accepted",
        "alert_name": normalized["alert_name"],
        "incident_id": incident.id,
        "datadog_monitor_id": normalized.get("monitor_id", ""),
    }


@router.post("/grafana")
async def grafana_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    x_grafana_signature: str = Header(default=""),
):
    """Receive a Grafana alert webhook, verify signature, create an Incident.

    Verifies HMAC-SHA256 signature using GRAFANA_WEBHOOK_SECRET.
    Only processes "firing" status (not "resolved" which is handled by callbacks).
    """
    from runbookai.config import settings

    raw_body = await request.body()
    if not verify_grafana_signature(raw_body, x_grafana_signature, settings.grafana_webhook_secret):
        logger.warning("Grafana webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    normalized = parse_grafana_payload(payload)
    if not normalized:
        return {"status": "ignored"}

    # Only create incidents for firing status; resolved is logged but not actioned
    status = normalized.get("status", "")
    if status != "firing":
        logger.info(
            "Grafana webhook %s for alert %s — logged but not actioned",
            status,
            normalized.get("alert_name", "unknown"),
        )
        return {
            "status": "logged",
            "event_type": status,
            "alert_uid": normalized.get("alert_uid", ""),
            "message": f"Status '{status}' logged but not actionable",
        }

    # Create incident for firing status
    incident = Incident(
        id=str(uuid.uuid4()),
        source="grafana",
        alert_name=normalized["alert_name"],
        alert_body=payload,
    )
    session.add(incident)
    await session.commit()
    await session.refresh(incident)

    background_tasks.add_task(run_agent_for_incident, incident.id)
    logger.info(
        "Grafana alert received: %s (alert_uid=%s runbookai_id=%s)",
        normalized["alert_name"],
        normalized.get("alert_uid", "?")[:16],
        incident.id,
    )
    return {
        "status": "accepted",
        "alert_name": normalized["alert_name"],
        "incident_id": incident.id,
        "grafana_alert_uid": normalized.get("alert_uid", ""),
    }


@router.post("/slack/test")
async def slack_test_webhook():
    """Test Slack webhook connectivity.

    Posts a test message to verify the configured SLACK_WEBHOOK_URL is working.
    Requires SLACK_WEBHOOK_URL to be set in config.

    Returns:
        {
            "success": bool,
            "status": str,
            "message": str,
        }
    """
    from runbookai.config import settings

    result = await test_slack_webhook(settings.slack_webhook_url)
    if result["success"]:
        logger.info("Slack webhook test passed")
    else:
        logger.error("Slack webhook test failed: %s", result["message"])
    return result
