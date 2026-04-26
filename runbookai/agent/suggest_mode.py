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
from typing import Any, Optional

import openai

from runbookai.agent.tools import (
    HIGH_RISK_TOOLS,
    SESSION_AWARE_TOOLS,
    TOOL_REGISTRY,
    TOOL_SCHEMAS_OPENAI,
)
from runbookai.config import settings
from runbookai.models import ApprovalRequest, ApprovalStatus, Incident
from runbookai.trace.recorder import AgentTraceRecorder

logger = logging.getLogger("runbookai.suggest_mode")

# Sentinel returned by propose_next_action when the agent calls `finish`.
RESOLVED = object()

_SYSTEM_PROMPT = """\
You are RunbookAI, an autonomous incident-response agent. Use your reasoning
and available tools to diagnose and remediate incidents effectively.

Rules:
- Think strategically: analyze the alert, examine available tools, and reason
  about the best diagnostic and remediation approach.
- Diagnose before remediating. Start with check_logs or http_check to understand
  the root cause.
- If a runbook is provided, use it as guidance (not a mandatory requirement).
  Trust your analysis if it suggests a better path.
- If similar past experiences are provided, learn from them but adapt to the
  current incident's unique context.
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
        # Set to True when this incident is a regression of a prior one.
        # Used in demo mode to return more alarming canned responses.
        self._is_regression: bool = False
        self._client = openai.AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )

    @classmethod
    async def create(cls, incident_id: str, session: Any) -> "SuggestModeAgent":
        """Async factory — construct agent and restore persisted messages if any."""
        agent = cls(incident_id, session)
        incident = await session.get(Incident, incident_id)
        if incident:
            if incident.messages_json:
                agent.messages = list(incident.messages_json)
                logger.info(
                    "incident=%s restored %d messages from DB",
                    incident_id,
                    len(agent.messages),
                )
            agent._is_regression = bool(incident.possible_regression)
        return agent

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
            model=settings.llm_model,
            messages=[{"role": "system", "content": _SYSTEM_PROMPT}] + self.messages,
            tools=TOOL_SCHEMAS_OPENAI,
            tool_choice="auto",
            max_tokens=4096,
        )

        choice = response.choices[0]
        message = choice.message

        # Persist assistant turn — must appear before any tool result.
        # Serialize tool_calls to plain dicts; the OpenAI SDK objects are not
        # JSON-serializable and cannot be stored in the DB as-is.
        tool_calls_json = None
        if message.tool_calls:
            tool_calls_json = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": tool_calls_json,
        }
        self.messages.append(assistant_msg)
        await self._persist_messages()

        finish_reason = choice.finish_reason

        tool_name: str = ""
        tool_input: dict[str, Any] = {}
        tool_use_id: str = ""
        rationale: str = message.content.strip() if message.content else "No rationale provided."

        # Try structured tool_calls first (OpenAI/Anthropic format).
        if message.tool_calls:
            tc = message.tool_calls[0]
            tool_name = tc.function.name
            tool_use_id = tc.id
            try:
                tool_input = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_input = {}
        # Fallback: try to parse tool call as JSON in content (Ollama workaround).
        elif message.content:
            try:
                # Strip markdown code block formatting if present.
                content = message.content.strip()
                if content.startswith("```"):
                    # Remove ```json or ``` at start and ``` at end
                    content = content.replace("```json\n", "").replace("```", "").strip()

                tool_json = json.loads(content)
                if isinstance(tool_json, dict) and "name" in tool_json:
                    tool_name = tool_json["name"]
                    tool_input = tool_json.get("arguments", {})
                    tool_use_id = str(uuid.uuid4())  # Generate ID for fallback case.
                    logger.info(
                        "incident=%s parsed tool call from content fallback: %s",
                        self.incident_id,
                        tool_name,
                    )
            except (json.JSONDecodeError, KeyError, AttributeError):
                pass

        # If still no tool found, return RESOLVED (end of conversation).
        if not tool_name:
            logger.warning(
                "incident=%s LLM returned finish_reason=%s without calling tools",
                self.incident_id,
                finish_reason,
            )
            return RESOLVED

        logger.info(
            "incident=%s proposed tool=%s input=%s",
            self.incident_id,
            tool_name,
            tool_input,
        )

        if tool_name == "finish":
            # Record the finish call and signal resolution to harness.
            await self._append_tool_result(
                tool_use_id,
                tool_name,
                tool_input,
                {"status": "ok"},
            )
            return RESOLVED

        return ProposedAction(
            tool_name=tool_name,
            tool_input=tool_input,
            rationale=rationale,
            tool_use_id=tool_use_id,
            is_high_risk=tool_name in HIGH_RISK_TOOLS,
        )

    async def execute_tool(self, action: ProposedAction) -> dict[str, Any]:
        """Execute a (non-paused) tool, record it, and append the result.

        Called by the harness for low-risk tools in suggest mode, and for
        ALL tools in autonomous mode.
        """
        result: dict[str, Any]
        try:
            # Demo mode: return pre-canned responses, no real SSH connections.
            if settings.demo_mode:
                from runbookai.agent.demo import get_demo_response

                result_raw = get_demo_response(
                    action.tool_name, action.tool_input, is_regression=self._is_regression
                )
                async with self.recorder.record(action.tool_name, action.tool_input) as capture:
                    result = result_raw
                    capture(result)
            else:
                fn = TOOL_REGISTRY[action.tool_name]
                # Inject the DB session for tools that need credential lookup.
                extra: dict[str, Any] = {}
                if action.tool_name in SESSION_AWARE_TOOLS:
                    extra["_session"] = self.session
                async with self.recorder.record(action.tool_name, action.tool_input) as capture:
                    result = await fn(**action.tool_input, **extra)
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
        row: Optional[ApprovalRequest] = await self.session.get(ApprovalRequest, approval_id)
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

    async def _persist_messages(self) -> None:
        """Serialize self.messages to the Incident row for crash recovery."""
        incident = await self.session.get(Incident, self.incident_id)
        if incident:
            incident.messages_json = list(self.messages)
            await self.session.commit()

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
        await self._persist_messages()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_initial_message(context: dict[str, Any]) -> str:
    alert = context.get("alert", {})
    runbook = context.get("runbook", "No runbook found.")
    previous = context.get("previous_actions", [])
    experiences = context.get("experiences", [])  # Phase 2A: retrieve experiences

    lines = [
        "## Incident Alert",
        f"Name: {alert.get('name', 'unknown')}",
        f"Service: {alert.get('service', 'unknown')}",
        f"Severity: {alert.get('severity', 'unknown')}",
        f"Details: {alert.get('details', '')}",
    ]

    # Phase 2A: Inject similar experiences as soft guidance
    if experiences:
        lines += [
            "",
            "## Similar Past Experiences (for reference)",
        ]
        for exp in experiences[:3]:  # Limit to top 3 for clarity
            lines.append(f"- Context: {exp.context[:100]}")
            lines.append(f"  Action: {exp.action}")
            lines.append(f"  Outcome: {exp.outcome}")
            lines.append(f"  Success: {exp.success}, Confidence: {exp.confidence}")

    # Phase 2B: Runbook is optional guidance, not mandatory
    lines += [
        "",
        "## Runbook (optional guidance)",
        runbook,
    ]

    if previous:
        lines += ["", "## Previous Actions (already taken this session)"]
        for a in previous:
            lines.append(
                f"- {a.get('tool_name')}: {a.get('tool_input')} → {a.get('tool_output')}"
            )

    regression = context.get("regression")
    if regression:
        lines += [
            "",
            "## POSSIBLE REGRESSION DETECTED",
            f"This service was remediated {regression.get('minutes_ago', '?')} minutes ago "
            f"(incident {regression.get('prior_incident_id', '?')}).",
            f"Prior resolution: {regression.get('prior_summary', 'unknown')}",
            "",
            "IMPORTANT: The prior restart may have only masked the root cause.",
            "Do NOT restart again without first diagnosing why the fix did not hold.",
            "Focus on finding the underlying root cause, not the symptom.",
        ]

    lines += [
        "",
        "Diagnose and remediate using your reasoning and available tools. Call finish() when done.",
    ]
    return "\n".join(lines)
