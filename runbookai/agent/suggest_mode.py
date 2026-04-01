"""Suggest Mode agent — proposes actions, waits for human approval before executing.

Architecture mirrors claw-code's decision loop:
  - Accumulates a `messages` list that grows each turn (full history passed
    to the model every iteration, exactly as claw-code does).
  - On each iteration the LLM returns finish_reason="tool_calls"; the agent
    extracts tool call objects from response.choices[0].message.tool_calls.
  - HIGH-RISK tools write an ApprovalRequest to DB and pause. The harness
    detects the WAITING_APPROVAL sentinel and returns control to the caller.
    When POST /approvals/{id}/approve arrives the harness calls
    resume_after_approval(), which replays the approved tool and continues.
  - Non-risky tools (check_logs, http_check) execute immediately without
    interruption — same as claw-code's straight-through dispatch.
  - Errors from tools are returned as tool messages with the error content
    so the model can re-plan; no silent retries.

Flow:
  1. propose_next_action(context) → builds messages, calls LLM, extracts first
     tool call, returns ProposedAction without executing.
  2. AgentHarness inspects: high-risk? → write ApprovalRequest, pause.
     low-risk? → call execute_tool() directly, append result, continue loop.
  3. resume_after_approval(approval_id) → load row, verify approved, execute,
     record via AgentTraceRecorder, return result dict.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import openai

from runbookai.agent.tools import HIGH_RISK_TOOLS, TOOL_REGISTRY, TOOL_SCHEMAS_OPENAI
from runbookai.config import settings
from runbookai.models import ApprovalRequest, ApprovalStatus
from runbookai.trace.recorder import AgentTraceRecorder

logger = logging.getLogger("runbookai.suggest_mode")

# Sentinel returned by propose_next_action when the agent calls `finish`.
RESOLVED = object()

_SYSTEM_PROMPT = """\
You are RunbookAI, an autonomous incident-response agent. You follow the
provided runbook step-by-step. Think carefully before acting.

Rules:
- Diagnose before remediating. Start with check_logs or http_check.
- When the incident is resolved (or you are certain human escalation is the
  right next step), call finish() with a clear summary.
- Never repeat a tool call that already succeeded.
- If a tool returns status="not_implemented", treat it as a no-op and continue.
"""


@dataclass
class ProposedAction:
    tool_name: str
    tool_input: dict[str, Any]
    rationale: str  # plain-English explanation shown to the approving human
    tool_use_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_high_risk: bool = False


class SuggestModeAgent:
    """Agent that accumulates message history and calls the LLM each turn.

    State is the `messages` list — the same pattern claw-code uses. Each tool
    result is appended as a `tool` role message before the next LLM call,
    giving the model full context.

    Usage (managed by AgentHarness — do not call directly):
        agent = SuggestModeAgent(incident_id, db_session)
        action = await agent.propose_next_action(context)
        if action is RESOLVED:
            ...
        elif action.is_high_risk:
            approval_id = await agent.create_approval_request(action, db_session)
            # pause — harness returns control to caller
        else:
            result = await agent.execute_tool(action)
    """

    def __init__(self, incident_id: str, session: Any) -> None:
        self.incident_id = incident_id
        self.session = session
        self.recorder = AgentTraceRecorder(session, incident_id)
        # Full conversation history; grows each turn (claw-code pattern).
        self.messages: list[dict[str, Any]] = []
        self._client = openai.AsyncOpenAI(
            base_url=settings.ollama_base_url,
            api_key="ollama",
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def propose_next_action(self, context: dict[str, Any]) -> ProposedAction | object:
        """Build/extend the message history, call the LLM, return the next action.

        `context` is only used to build the FIRST user message. Subsequent
        calls operate purely on the accumulated `self.messages` list.

        Returns:
            ProposedAction  — agent wants to call a tool.
            RESOLVED        — agent called finish(); incident is done.
        """
        if not self.messages:
            # First turn: construct the initial user message from context.
            self.messages.append(
                {
                    "role": "user",
                    "content": _build_initial_message(context),
                }
            )

        response = await self._client.chat.completions.create(
            model=settings.ollama_model,
            messages=[{"role": "system", "content": _SYSTEM_PROMPT}] + self.messages,
            tools=TOOL_SCHEMAS_OPENAI,
            tool_choice="required",
            max_tokens=4096,
        )

        choice = response.choices[0]
        message = choice.message

        # Persist assistant turn — must appear before any tool result.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": message.tool_calls,
        }
        self.messages.append(assistant_msg)

        finish_reason = choice.finish_reason

        if finish_reason == "stop" or not message.tool_calls:
            # Model finished without calling a tool — treat as unresolved.
            logger.warning(
                "incident=%s LLM returned finish_reason=%s without tool call",
                self.incident_id,
                finish_reason,
            )
            return RESOLVED

        # Extract the first tool call (OpenAI format).
        tc = message.tool_calls[0]
        tool_name: str = tc.function.name
        try:
            tool_input: dict[str, Any] = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            tool_input = {}

        rationale: str = message.content.strip() if message.content else "No rationale provided."

        logger.info(
            "incident=%s proposed tool=%s input=%s",
            self.incident_id,
            tool_name,
            tool_input,
        )

        if tool_name == "finish":
            # Record the finish call and signal resolution to harness.
            await self._append_tool_result(
                tc.id,
                tool_name,
                tool_input,
                {"status": "ok"},
            )
            return RESOLVED

        return ProposedAction(
            tool_name=tool_name,
            tool_input=tool_input,
            rationale=rationale,
            tool_use_id=tc.id,
            is_high_risk=tool_name in HIGH_RISK_TOOLS,
        )

    async def execute_tool(self, action: ProposedAction) -> dict[str, Any]:
        """Execute a (non-paused) tool, record it, and append the result.

        Called by the harness for low-risk tools in suggest mode, and for
        ALL tools in autonomous mode.
        """
        result: dict[str, Any]
        try:
            fn = TOOL_REGISTRY[action.tool_name]
            async with self.recorder.record(action.tool_name, action.tool_input) as capture:
                result = await fn(**action.tool_input)
                capture(result)
        except KeyError:
            result = {"status": "error", "error": f"unknown tool: {action.tool_name}"}
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "incident=%s tool=%s raised", self.incident_id, action.tool_name
            )
            result = {"status": "error", "error": str(exc)}

        await self._append_tool_result(
            action.tool_use_id, action.tool_name, action.tool_input, result
        )
        return result

    async def create_approval_request(
        self, action: ProposedAction
    ) -> str:
        """Write an ApprovalRequest row and return its ID.

        The harness pauses after calling this and waits for
        POST /approvals/{id}/approve.
        """
        approval = ApprovalRequest(
            incident_id=self.incident_id,
            tool_name=action.tool_name,
            tool_input=action.tool_input,
            rationale=action.rationale,
            status=ApprovalStatus.PENDING,
        )
        self.session.add(approval)
        await self.session.commit()
        await self.session.refresh(approval)
        logger.info(
            "incident=%s approval_request=%s tool=%s (pending)",
            self.incident_id,
            approval.id,
            action.tool_name,
        )
        return approval.id

    async def resume_after_approval(self, approval_id: str) -> dict[str, Any]:
        """Execute an approved action and return the result.

        Called by POST /approvals/{id}/approve after the DB row is updated.

        Steps:
          1. Load ApprovalRequest from DB.
          2. Verify status == "approved".
          3. Execute via TOOL_REGISTRY with trace recording.
          4. Append tool result to messages so the loop can continue.
        """
        row: ApprovalRequest | None = await self.session.get(ApprovalRequest, approval_id)
        if row is None:
            raise ValueError(f"ApprovalRequest {approval_id} not found")
        if row.status != ApprovalStatus.APPROVED:
            raise ValueError(
                f"ApprovalRequest {approval_id} has status={row.status}, expected approved"
            )

        action = ProposedAction(
            tool_name=row.tool_name,
            tool_input=dict(row.tool_input),
            rationale=row.rationale,
            # We don't have the original tool_use_id stored — generate a new
            # one for the tool result message (OpenAI allows this in
            # resumed flows where the original id is already in history).
            tool_use_id=row.id,
            is_high_risk=row.tool_name in HIGH_RISK_TOOLS,
        )
        result = await self.execute_tool(action)
        logger.info(
            "incident=%s approval=%s executed tool=%s result_status=%s",
            self.incident_id,
            approval_id,
            row.tool_name,
            result.get("status"),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _append_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Append a tool result message (OpenAI multi-turn format)."""
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(result),
            }
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_initial_message(context: dict[str, Any]) -> str:
    alert = context.get("alert", {})
    runbook = context.get("runbook", "No runbook found.")
    previous = context.get("previous_actions", [])

    lines = [
        "## Incident Alert",
        f"Name: {alert.get('name', 'unknown')}",
        f"Service: {alert.get('service', 'unknown')}",
        f"Severity: {alert.get('severity', 'unknown')}",
        f"Details: {alert.get('details', '')}",
        "",
        "## Runbook",
        runbook,
    ]

    if previous:
        lines += ["", "## Previous Actions (already taken this session)"]
        for a in previous:
            lines.append(f"- {a.get('tool_name')}: {a.get('tool_input')} → {a.get('tool_output')}")

    lines += [
        "",
        "Diagnose and remediate following the runbook. Call finish() when done.",
    ]
    return "\n".join(lines)
