"""Approval endpoints — human approves or rejects a proposed agent action (Suggest Mode)."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("runbookai.api.approvals")
router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.post("/{action_id}/approve")
async def approve_action(action_id: str):
    """Approve a proposed action. The agent will execute it immediately after.

    TODO:
    1. Load ApprovalRequest from DB
    2. Verify status == "pending"
    3. Set status = "approved", decided_at = now
    4. Signal the waiting agent coroutine to proceed
    """
    # TODO: implement DB lookup + approval signal
    logger.info("Action approved: %s", action_id)
    return {"status": "approved", "action_id": action_id, "decided_at": datetime.utcnow()}


@router.post("/{action_id}/reject")
async def reject_action(action_id: str, reason: str = ""):
    """Reject a proposed action. The agent will skip it and propose an alternative.

    TODO:
    1. Load ApprovalRequest from DB
    2. Verify status == "pending"
    3. Set status = "rejected", decided_at = now
    4. Signal the waiting agent coroutine to skip + re-plan
    """
    logger.info("Action rejected: %s reason=%s", action_id, reason)
    return {"status": "rejected", "action_id": action_id, "decided_at": datetime.utcnow()}


@router.get("/pending")
async def list_pending_approvals():
    """List all actions currently waiting for human approval.

    TODO: Query DB for ApprovalRequests with status == "pending".
    """
    return {"approvals": []}  # TODO
