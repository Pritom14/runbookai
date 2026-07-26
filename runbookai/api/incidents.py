"""Incident endpoints — list, detail, and AgentTrace replay."""

import logging
import pathlib
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.cloud.auth import get_customer_from_api_key
from runbookai.database import get_session
from runbookai.models import AgentAction, Incident

logger = logging.getLogger("runbookai.api.incidents")
router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
async def list_incidents(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
):
    """List incidents, optionally filtered by customer.

    If X-API-Key header provided: returns only incidents for that customer.
    If no API key: returns all non-cloud incidents (customer_id is NULL).
    """
    customer_id: Optional[str] = None
    has_api_key = bool(x_api_key)
    logger.info("list_incidents: limit=%d offset=%d has_api_key=%s", limit, offset, has_api_key)

    # If API key provided, filter by customer
    if x_api_key:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer:
            raise HTTPException(status_code=401, detail="Invalid API key")
        customer_id = customer.id

    # Build query based on whether we're filtering by customer
    if customer_id:
        query = (
            select(Incident)
            .where(Incident.customer_id == customer_id)
            .order_by(Incident.created_at.desc())
        )
    else:
        # Non-API-key requests see only non-cloud incidents
        query = (
            select(Incident)
            .where(Incident.customer_id.is_(None))
            .order_by(Incident.created_at.desc())
        )

    result = await session.execute(query.limit(limit).offset(offset))
    incidents = result.scalars().all()

    return {
        "incidents": [
            {
                "id": i.id,
                "alert_name": i.alert_name,
                "status": i.status,
                "source": i.source,
                "created_at": i.created_at,
                "resolved_at": i.resolved_at,
            }
            for i in incidents
        ],
        "customer_id": customer_id,
    }


@router.get("/{incident_id}")
async def get_incident(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
):
    """Get incident details, with customer isolation.

    If X-API-Key provided: only accessible if customer matches API key.
    If no API key: only accessible if incident is non-cloud (customer_id is NULL).
    """
    logger.info("get_incident: incident_id=%s has_api_key=%s", incident_id, bool(x_api_key))
    incident = await session.get(Incident, incident_id)
    if incident is None:
        logger.warning("get_incident: incident not found: %s", incident_id)
        raise HTTPException(status_code=404, detail="Incident not found")

    # Check customer isolation
    if x_api_key:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer or incident.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        # Non-API-key access only to non-cloud incidents
        if incident.customer_id is not None:
            raise HTTPException(status_code=403, detail="Access denied")

    return {
        "id": incident.id,
        "alert_name": incident.alert_name,
        "status": incident.status,
        "source": incident.source,
        "summary": incident.summary,
        "alert_body": incident.alert_body,
        "created_at": incident.created_at,
        "resolved_at": incident.resolved_at,
        "customer_id": incident.customer_id,
    }


@router.get("/{incident_id}/replay/ui", response_class=HTMLResponse)
async def replay_ui(incident_id: str):
    html = (pathlib.Path(__file__).parent.parent / "static" / "replay.html").read_text()
    return HTMLResponse(html.replace("__INCIDENT_ID__", incident_id))


@router.get("/{incident_id}/replay")
async def get_incident_replay(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
):
    """AgentTrace — full chronological timeline of every agent action.

    With customer isolation: only accessible with proper API key.
    """
    logger.info("get_incident_replay: incident_id=%s has_api_key=%s", incident_id, bool(x_api_key))
    incident = await session.get(Incident, incident_id)
    if incident is None:
        logger.warning("get_incident_replay: incident not found: %s", incident_id)
        raise HTTPException(status_code=404, detail="Incident not found")

    # Check customer isolation
    if x_api_key:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer or incident.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        # Non-API-key access only to non-cloud incidents
        if incident.customer_id is not None:
            raise HTTPException(status_code=403, detail="Access denied")

    result = await session.execute(
        select(AgentAction)
        .where(AgentAction.incident_id == incident_id)
        .order_by(AgentAction.created_at)
    )
    actions = result.scalars().all()

    base_time = actions[0].created_at if actions else incident.created_at

    timeline = []
    for action in actions:
        t_seconds = int((action.created_at - base_time).total_seconds())
        event_name = None
        if action.tool_name == "_event":
            event_name = (action.tool_input or {}).get("event", "event")

        step = {
            "t_seconds": t_seconds,
            "timestamp_offset": float(t_seconds),
            "tool": action.tool_name,
            "tool_name": None if action.tool_name == "_event" else action.tool_name,
            "event": event_name,
            "input": action.tool_input,
            "output": action.tool_output,
            "duration_ms": action.duration_ms,
            "timestamp": action.created_at,
        }

        if event_name and isinstance(action.tool_output, dict):
            step.update(action.tool_output)

        timeline.append(step)

    return {
        "incident_id": incident_id,
        "alert_name": incident.alert_name,
        "status": incident.status,
        "created_at": incident.created_at,
        "resolved_at": incident.resolved_at,
        "summary": incident.summary,
        "customer_id": incident.customer_id,
        "timeline": timeline,
    }
