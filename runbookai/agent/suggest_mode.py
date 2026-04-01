"""Suggest Mode agent — proposes actions, waits for human approval before executing.

Flow:
  1. Agent decides next action (tool + input + rationale)
  2. ApprovalRequest written to DB with status=pending
  3. Agent pauses — returns control to caller
  4. Human calls POST /approvals/{id}/approve or /reject
  5. Agent resumes and executes (or skips) the action

This is the default and recommended mode. Build trust before switching to autonomous.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger("runbookai.suggest_mode")


@dataclass
class ProposedAction:
    tool_name: str
    tool_input: dict
    rationale: str  # plain-English explanation shown to the approving human


class SuggestModeAgent:
    """Agent that proposes actions and waits for explicit approval.

    TODO: Wire to AgentHarness once LLM decision loop is implemented.
    """

    def __init__(self, incident_id: str):
        self.incident_id = incident_id

    async def propose_next_action(self, context: dict) -> ProposedAction:
        """Ask the LLM what to do next given the current incident context.

        TODO: Call Token0 proxy → Anthropic Claude with tool-use.
        Return a ProposedAction with the chosen tool + rationale.

        context = {
            "alert": {...},
            "runbook": "...",
            "previous_actions": [...],
        }
        """
        raise NotImplementedError("TODO: implement LLM decision loop via Token0")

    async def execute_approved_action(self, approval_id: str) -> dict:
        """Execute an action after it has been approved.

        TODO:
        1. Load ApprovalRequest from DB by approval_id
        2. Verify status == "approved"
        3. Call TOOL_REGISTRY[tool_name](**tool_input)
        4. Write AgentAction to DB via AgentTraceRecorder
        5. Return tool output
        """
        raise NotImplementedError("TODO: implement approved action execution")
