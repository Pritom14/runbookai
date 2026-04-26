"""Approval endpoints — human approves or rejects a proposed agent action (Suggest Mode)."""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import ApprovalRequest, ApprovalStatus, Incident
from runbookai.slack import send_slack_notification
from runbookai.trace.recorder import AgentTraceRecorder

logger = logging.getLogger("runbookai.api.approvals")
router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.post("/{action_id}/approve")
async def approve_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Approve a proposed action. The agent will execute it immediately after."""
    approval: Optional[ApprovalRequest] = await session.get(ApprovalRequest, action_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"ApprovalRequest {action_id} not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"ApprovalRequest has status={approval.status}, expected pending",
        )

    approval.status = ApprovalStatus.APPROVED
    approval.decided_at = datetime.utcnow()
    await session.commit()
    await session.refresh(approval)

    recorder = AgentTraceRecorder(session, approval.incident_id)
    await recorder.log_event(
        "approval_granted",
        {"tool": approval.tool_name, "approval_id": action_id},
    )
    incident = await session.get(Incident, approval.incident_id)
    if incident:
        await send_slack_notification(
            "approval_granted", incident, {"tool": approval.tool_name}
        )

    # Re-enter the agent harness for this incident.
    from runbookai.agent.harness import AgentHarness

    harness = AgentHarness(incident_id=approval.incident_id)
    try:
        result = await harness.resume_incident(action_id, session)
        logger.info(
            "action approved and resumed: action_id=%s incident=%s resolved=%s",
            action_id,
            approval.incident_id,
            result.resolved,
        )
    except Exception as exc:
        logger.exception("failed to resume incident after approval: %s", exc)
        raise HTTPException(status_code=500, detail=f"Agent resume failed: {exc}") from exc

    return {
        "status": "approved",
        "action_id": action_id,
        "incident_id": approval.incident_id,
        "decided_at": approval.decided_at,
        "agent_result": {
            "resolved": result.resolved,
            "summary": result.summary,
        },
    }


@router.post("/{action_id}/reject")
async def reject_action(
    action_id: str,
    reason: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Reject a proposed action. The agent will skip it and propose an alternative."""
    approval: Optional[ApprovalRequest] = await session.get(ApprovalRequest, action_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"ApprovalRequest {action_id} not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"ApprovalRequest has status={approval.status}, expected pending",
        )

    approval.status = ApprovalStatus.REJECTED
    approval.decided_at = datetime.utcnow()
    await session.commit()
    await session.refresh(approval)

    recorder = AgentTraceRecorder(session, approval.incident_id)
    await recorder.log_event(
        "approval_rejected",
        {"tool": approval.tool_name, "approval_id": action_id, "reason": reason},
    )
    incident = await session.get(Incident, approval.incident_id)
    if incident:
        await send_slack_notification(
            "approval_rejected", incident, {"tool": approval.tool_name, "reason": reason}
        )

    logger.info(
        "action rejected: action_id=%s incident=%s reason=%s",
        action_id,
        approval.incident_id,
        reason,
    )
    return {
        "status": "rejected",
        "action_id": action_id,
        "incident_id": approval.incident_id,
        "decided_at": approval.decided_at,
        "reason": reason,
    }


@router.get("/pending")
async def list_pending_approvals(session: AsyncSession = Depends(get_session)):
    """List all actions currently waiting for human approval."""
    result = await session.execute(
        select(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING)
    )
    approvals = result.scalars().all()
    return {
        "approvals": [
            {
                "id": a.id,
                "incident_id": a.incident_id,
                "tool_name": a.tool_name,
                "tool_input": a.tool_input,
                "rationale": a.rationale,
                "created_at": a.created_at,
            }
            for a in approvals
        ]
    }
