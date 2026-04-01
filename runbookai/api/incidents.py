"""Incident endpoints — list, detail, and AgentTrace replay."""

import logging

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("runbookai.api.incidents")
router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
async def list_incidents(limit: int = 50, offset: int = 0):
    """List incidents, most recent first.

    TODO: Query DB with pagination.
    """
    return {"incidents": [], "total": 0}  # TODO


@router.get("/{incident_id}")
async def get_incident(incident_id: str):
    """Get full incident detail including current status.

    TODO: Load from DB, 404 if not found.
    """
    raise HTTPException(status_code=404, detail="Not implemented yet")


@router.get("/{incident_id}/replay")
async def get_incident_replay(incident_id: str):
    """AgentTrace — full timeline of every action the agent took.

    Returns a chronological list of actions:
        [
            {"t": 0, "tool": "check_logs", "input": {...}, "output": {...}, "duration_ms": 340},
            {"t": 12, "tool": "restart_service", "input": {...}, "output": {...}, "duration_ms": 1200},
            ...
        ]

    TODO: Load AgentActions from DB for this incident, sorted by created_at.
    """
    raise HTTPException(status_code=404, detail="Not implemented yet")
