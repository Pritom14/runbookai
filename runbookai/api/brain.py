"""Company Brain API — ingest operational knowledge and experience memory.

Endpoints for ingesting postmortems, runbook edits, and other operational signals
that enhance the agent's decision-making through experience memory.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.brain.postmortem_parser import parse_postmortem
from runbookai.cloud.auth import get_customer_from_api_key
from runbookai.database import get_session
from runbookai.models import Incident, Postmortem, Runbook, RunbookVersion

logger = logging.getLogger("runbookai.api.brain")
router = APIRouter(prefix="/brain", tags=["brain"])


class PostmorttemIngestRequest(BaseModel):
    """Request to ingest a postmortem document."""

    markdown_content: str  # The postmortem document as markdown


class RunbookVersionRequest(BaseModel):
    """Request to create a new runbook version after incident-driven changes."""

    content: str  # Updated runbook content
    incident_id: Optional[str] = None  # The incident that prompted this change
    change_description: Optional[str] = None  # What changed and why


@router.post("/incidents/{incident_id}/postmortem", status_code=201)
async def ingest_postmortem(
    incident_id: str,
    body: PostmorttemIngestRequest,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
):
    """Ingest a postmortem document for an incident.

    This endpoint accepts a postmortem markdown document, parses it to extract
    structured knowledge (root cause, remediation steps, lessons), and stores it
    in the experience memory for the agent to learn from.

    Args:
        incident_id: The incident this postmortem relates to
        body: PostmorttemIngestRequest with markdown_content
        x_api_key: Optional API key for customer isolation

    Returns:
        Stored postmortem with extracted metadata
    """
    # Fetch the incident
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Check customer isolation
    customer_id: Optional[str] = None
    if x_api_key:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer or incident.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="Access denied")
        customer_id = customer.id
    else:
        # Non-API-key requests only to non-cloud incidents
        if incident.customer_id is not None:
            raise HTTPException(status_code=403, detail="Access denied")

    # Parse the postmortem
    parsed = parse_postmortem(body.markdown_content)

    # Store in database
    pm = Postmortem(
        incident_id=incident_id,
        customer_id=customer_id,
        markdown_content=body.markdown_content,
        root_cause=parsed["root_cause"],
        remediation_steps=parsed["remediation_steps"],
        timeline=parsed["timeline"],
        lessons=parsed["lessons"],
    )
    session.add(pm)
    await session.commit()
    await session.refresh(pm)

    logger.info(
        "postmortem ingested: incident_id=%s, customer_id=%s, pm_id=%s",
        incident_id,
        customer_id,
        pm.id,
    )

    return {
        "id": pm.id,
        "incident_id": pm.incident_id,
        "customer_id": pm.customer_id,
        "root_cause": pm.root_cause,
        "remediation_steps": pm.remediation_steps,
        "timeline": pm.timeline,
        "lessons": pm.lessons,
        "created_at": pm.created_at,
    }


@router.post("/runbooks/{runbook_id}/versions", status_code=201)
async def create_runbook_version(
    runbook_id: str,
    body: RunbookVersionRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new version of a runbook after incident-driven changes.

    Tracks when a customer edits a runbook following an incident or postmortem.
    This allows the agent to learn: "when pattern Z fires, we now use strategy B
    (learned from postmortem of incident Y)".

    Args:
        runbook_id: The runbook being edited
        body: RunbookVersionRequest with updated content
        session: Database session

    Returns:
        Created version record
    """
    # Fetch the runbook
    rb = await session.get(Runbook, runbook_id)
    if rb is None:
        raise HTTPException(status_code=404, detail="Runbook not found")

    # Get the previous version count to determine the new version number
    result = await session.execute(
        select(RunbookVersion)
        .where(RunbookVersion.runbook_id == runbook_id)
        .order_by(RunbookVersion.version.desc())
    )
    prev_version_row = result.scalars().first()
    new_version_num = (prev_version_row.version + 1) if prev_version_row else 1

    # Create the new version
    version = RunbookVersion(
        runbook_id=runbook_id,
        version=new_version_num,
        content=body.content,
        incident_id=body.incident_id,
        change_description=body.change_description,
        previous_content=rb.content,
    )
    session.add(version)

    # Also update the runbook's current content
    rb.content = body.content
    session.add(rb)

    await session.commit()
    await session.refresh(version)

    logger.info(
        "runbook version created: runbook_id=%s, version=%d, incident_id=%s",
        runbook_id,
        new_version_num,
        body.incident_id,
    )

    return {
        "id": version.id,
        "runbook_id": version.runbook_id,
        "version": version.version,
        "incident_id": version.incident_id,
        "change_description": version.change_description,
        "created_at": version.created_at,
    }


@router.get("/incidents/{incident_id}/postmortem")
async def get_postmortem(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
):
    """Retrieve the postmortem for an incident if one exists.

    Args:
        incident_id: The incident to fetch postmortem for
        x_api_key: Optional API key for customer isolation

    Returns:
        Postmortem data if found, 404 if not found
    """
    # Fetch the incident
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Check customer isolation
    if x_api_key:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer or incident.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="Access denied")
    else:
        # Non-API-key requests only to non-cloud incidents
        if incident.customer_id is not None:
            raise HTTPException(status_code=403, detail="Access denied")

    # Fetch postmortem
    result = await session.execute(
        select(Postmortem).where(Postmortem.incident_id == incident_id)
    )
    pm = result.scalars().first()

    if pm is None:
        raise HTTPException(status_code=404, detail="Postmortem not found for this incident")

    return {
        "id": pm.id,
        "incident_id": pm.incident_id,
        "customer_id": pm.customer_id,
        "root_cause": pm.root_cause,
        "remediation_steps": pm.remediation_steps,
        "timeline": pm.timeline,
        "lessons": pm.lessons,
        "markdown_content": pm.markdown_content,
        "created_at": pm.created_at,
        "updated_at": pm.updated_at,
    }
