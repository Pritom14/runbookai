"""Incident endpoints — list, detail, and AgentTrace replay."""

import logging
import pathlib

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import AgentAction, Incident

logger = logging.getLogger("runbookai.api.incidents")
router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
async def list_incidents(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Incident).order_by(Incident.created_at.desc()).limit(limit).offset(offset)
    )
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
        ]
    }


@router.get("/{incident_id}")
async def get_incident(incident_id: str, session: AsyncSession = Depends(get_session)):
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "id": incident.id,
        "alert_name": incident.alert_name,
        "status": incident.status,
        "source": incident.source,
        "summary": incident.summary,
        "alert_body": incident.alert_body,
        "created_at": incident.created_at,
        "resolved_at": incident.resolved_at,
    }


@router.get("/{incident_id}/replay/ui", response_class=HTMLResponse)
async def replay_ui(incident_id: str):
    html = (pathlib.Path(__file__).parent.parent / "static" / "replay.html").read_text()
    return HTMLResponse(html.replace("__INCIDENT_ID__", incident_id))


@router.get("/{incident_id}/replay")
async def get_incident_replay(incident_id: str, session: AsyncSession = Depends(get_session)):
    """AgentTrace — full chronological timeline of every agent action."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    result = await session.execute(
        select(AgentAction)
        .where(AgentAction.incident_id == incident_id)
        .order_by(AgentAction.created_at)
    )
    actions = result.scalars().all()

    base_time = actions[0].created_at if actions else incident.created_at

    return {
        "incident_id": incident_id,
        "alert_name": incident.alert_name,
        "status": incident.status,
        "created_at": incident.created_at,
        "resolved_at": incident.resolved_at,
        "summary": incident.summary,
        "timeline": [
            {
                "t_seconds": int((a.created_at - base_time).total_seconds()),
                "tool": a.tool_name,
                "input": a.tool_input,
                "output": a.tool_output,
                "duration_ms": a.duration_ms,
                "timestamp": a.created_at,
            }
            for a in actions
        ],
    }
