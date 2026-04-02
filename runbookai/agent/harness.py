"""AgentHarness — top-level controller that runs an incident response session.

Architecture mirrors claw-code's agent loop:

  while step < MAX_STEPS:
      action = await agent.propose_next_action(context)
      if action is RESOLVED:
          break
      if action.is_high_risk and suggest_mode:
          approval_id = await agent.create_approval_request(action)
          update incident status = WAITING_APPROVAL
          break  ← harness pauses; resumes via resume_incident()
      else:
          result = await agent.execute_tool(action)
          step += 1

Exit conditions (matching claw-code):
  - LLM calls finish()                     → resolved=True
  - LLM returns end_turn without finish()  → resolved=False, escalate
  - MAX_STEPS reached                      → resolved=False, escalate
  - High-risk tool in suggest_mode         → pause, wait for approval

Resumption:
  POST /approvals/{id}/approve triggers resume_incident(approval_id).
  The harness re-enters the loop from the same SuggestModeAgent instance.
  Active agents are kept in _ACTIVE_AGENTS (in-memory dict). This is
  intentionally simple; a production version would serialize messages to DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from runbookai.agent.suggest_mode import RESOLVED, SuggestModeAgent
from runbookai.config import settings
from runbookai.models import IncidentStatus

logger = logging.getLogger("runbookai.harness")

# In-memory registry of active SuggestModeAgent instances keyed by incident_id.
# Allows resume_incident() to re-enter the same agent (same message history).
_ACTIVE_AGENTS: dict[str, SuggestModeAgent] = {}


@dataclass
class IncidentResult:
    incident_id: str
    resolved: bool
    summary: str
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    escalation_reason: str | None = None


class AgentHarness:
    """Runs a full incident response session for a given incident.

    Usage:
        harness = AgentHarness(incident_id="...", suggest_mode=True)
        result = await harness.run(db_session)

    Suggest Mode (suggest_mode=True, default):
        All high-risk tool calls pause and write an ApprovalRequest.
        The run() coroutine returns an IncidentResult with
        resolved=False and a "waiting_approval" summary.
        Call resume_incident(approval_id, db_session) after the human approves.

    Autonomous Mode (suggest_mode=False):
        All tools execute immediately. Still traces every action via
        AgentTraceRecorder. Use only in trusted/test environments.
    """

    MAX_STEPS = 10

    def __init__(
        self,
        incident_id: str,
        suggest_mode: bool | None = None,
    ) -> None:
        self.incident_id = incident_id
        # Default to global setting if not explicitly overridden.
        self.suggest_mode = suggest_mode if suggest_mode is not None else settings.suggest_mode

    async def run(self, session: Any) -> IncidentResult:
        """Entry point — run the full incident response loop.

        Parameters
        ----------
        session:
            An async SQLAlchemy session for DB reads/writes. The caller is
            responsible for session lifecycle.
        """
        # Load incident from DB.
        incident = await self._load_incident(session)
        alert_context = {
            "name": incident.alert_name,
            "service": incident.alert_body.get("service", "unknown"),
            "severity": incident.alert_body.get("severity", "unknown"),
            "details": str(incident.alert_body),
        }

        # Fetch the matching runbook.
        runbook_text = await self._load_runbook(incident.alert_name, session=session)

        # Build initial context dict passed to propose_next_action on step 0.
        context: dict[str, Any] = {
            "alert": alert_context,
            "runbook": runbook_text,
            "previous_actions": [],
        }

        # Create (or retrieve existing) agent for this incident.
        agent = _ACTIVE_AGENTS.get(self.incident_id)
        if agent is None:
            agent = await SuggestModeAgent.create(self.incident_id, session)
            _ACTIVE_AGENTS[self.incident_id] = agent

        # Update incident status to in_progress.
        incident.status = IncidentStatus.IN_PROGRESS
        await session.commit()

        actions_taken: list[dict[str, Any]] = []
        resolution_summary: str = ""

        for step in range(self.MAX_STEPS):
            logger.info("incident=%s step=%d/%d", self.incident_id, step + 1, self.MAX_STEPS)

            action = await agent.propose_next_action(context)

            if action is RESOLVED:
                # LLM called finish() or returned end_turn.
                resolution_summary = "Agent signalled resolution via finish()."
                incident.status = IncidentStatus.RESOLVED
                incident.resolved_at = datetime.utcnow()
                incident.summary = resolution_summary
                await session.commit()
                _ACTIVE_AGENTS.pop(self.incident_id, None)
                logger.info("incident=%s resolved at step %d", self.incident_id, step + 1)
                return IncidentResult(
                    incident_id=self.incident_id,
                    resolved=True,
                    summary=resolution_summary,
                    actions_taken=actions_taken,
                )

            # At this point action is a ProposedAction.
            if self.suggest_mode and action.is_high_risk:
                # Pause and write approval request.
                approval_id = await agent.create_approval_request(action)
                incident.status = IncidentStatus.WAITING_APPROVAL
                await session.commit()
                logger.info(
                    "incident=%s paused for approval=%s tool=%s",
                    self.incident_id,
                    approval_id,
                    action.tool_name,
                )
                return IncidentResult(
                    incident_id=self.incident_id,
                    resolved=False,
                    summary=(
                        f"Waiting for approval of {action.tool_name} "
                        f"(approval_id={approval_id}). Rationale: {action.rationale}"
                    ),
                    actions_taken=actions_taken,
                )

            # Execute tool (low-risk, or autonomous mode).
            result = await agent.execute_tool(action)
            actions_taken.append(
                {
                    "step": step + 1,
                    "tool_name": action.tool_name,
                    "tool_input": action.tool_input,
                    "tool_output": result,
                }
            )
            # After first iteration context.previous_actions is no longer
            # used — the agent's message history carries full state.

        # MAX_STEPS exhausted without resolution.
        escalation_reason = f"Reached MAX_STEPS ({self.MAX_STEPS}) without resolving incident."
        await self._escalate(session, incident, escalation_reason)
        _ACTIVE_AGENTS.pop(self.incident_id, None)
        return IncidentResult(
            incident_id=self.incident_id,
            resolved=False,
            summary=escalation_reason,
            actions_taken=actions_taken,
            escalation_reason=escalation_reason,
        )

    async def resume_incident(self, approval_id: str, session: Any) -> IncidentResult:
        """Continue the decision loop after a human approves a high-risk action.

        Called by POST /approvals/{id}/approve once the DB row is set to
        status="approved". Re-enters the same SuggestModeAgent (same message
        history) and runs the loop to the next pause or resolution.
        """
        agent = _ACTIVE_AGENTS.get(self.incident_id)
        if agent is None:
            # Agent was evicted (e.g. server restart). Reconstruct from DB,
            # restoring persisted message history for crash recovery.
            agent = await SuggestModeAgent.create(self.incident_id, session)
            _ACTIVE_AGENTS[self.incident_id] = agent

        # Execute the approved action and inject result into message history.
        await agent.resume_after_approval(approval_id)

        # Re-enter the main loop (pass empty context — agent uses its history).
        return await self.run(session)

    # ------------------------------------------------------------------
    # Private helpers (stubs — real infra needed)
    # ------------------------------------------------------------------

    async def _load_incident(self, session: Any) -> Any:
        """Load the Incident row from DB.

        TODO: Replace stub with real SQLAlchemy async get.
        """
        from runbookai.models import Incident

        incident = await session.get(Incident, self.incident_id)
        if incident is None:
            raise ValueError(f"Incident {self.incident_id} not found")
        return incident

    async def _load_runbook(self, alert_name: str, session: Any = None) -> str:
        """Fetch the runbook for this alert type.

        Priority:
        1. DB runbooks — first row whose alert_pattern is a substring of alert_name.
        2. Repo-level `runbooks/` directory (slug-matched YAML/MD/TXT file).
        3. _DEFAULT_RUNBOOK built-in fallback.
        """
        import pathlib
        import re

        # 1. DB lookup (requires a live session).
        if session is not None:
            try:
                from sqlalchemy import select as _select

                from runbookai.models import Runbook

                result = await session.execute(_select(Runbook))
                for rb in result.scalars().all():
                    if rb.alert_pattern and rb.alert_pattern.lower() in alert_name.lower():
                        logger.info("runbook loaded from DB: id=%s name=%s", rb.id, rb.name)
                        return rb.content
            except Exception:
                logger.exception("DB runbook lookup failed, falling through to file search")

        # 2. File-system lookup.
        runbooks_dir = pathlib.Path(__file__).parent.parent.parent / "runbooks"
        slug = re.sub(r"[^a-z0-9]+", "-", alert_name.lower()).strip("-")
        for ext in (".yaml", ".md", ".txt"):
            path = runbooks_dir / f"{slug}{ext}"
            if path.exists():
                logger.info("runbook loaded from file: %s", path)
                return path.read_text()

        logger.info("runbook not found for alert_name=%s, using default", alert_name)
        return _DEFAULT_RUNBOOK

    async def _escalate(self, session: Any, incident: Any, reason: str) -> None:
        """Mark incident as escalated and send an email notification if configured."""
        incident.status = IncidentStatus.ESCALATED
        incident.summary = reason
        await session.commit()
        logger.warning("incident=%s escalated: %s", self.incident_id, reason)

        if settings.escalation_email and settings.smtp_host:
            import email.message
            import smtplib

            msg = email.message.EmailMessage()
            msg["Subject"] = f"[RunbookAI] Escalation: {incident.alert_name}"
            msg["From"] = settings.smtp_user
            msg["To"] = settings.escalation_email
            msg.set_content(
                f"Incident: {incident.alert_name}\nReason: {reason}\nID: {incident.id}"
            )

            try:
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
                    s.starttls()
                    s.login(settings.smtp_user, settings.smtp_password)
                    s.send_message(msg)
                logger.info("escalation email sent to %s", settings.escalation_email)
            except Exception:
                logger.exception("failed to send escalation email")
        else:
            logger.warning("escalation_email or smtp_host not set — skipping email")

        if settings.slack_webhook_url:
            import httpx

            payload = {
                "text": (
                    f":rotating_light: *RunbookAI Escalation*\n"
                    f"Incident: `{incident.alert_name}`\n"
                    f"Status: {incident.status}\n"
                    f"Summary: {reason or 'No summary available.'}"
                )
            }
            async with httpx.AsyncClient() as client:
                await client.post(settings.slack_webhook_url, json=payload, timeout=10.0)
            logger.info("incident=%s slack escalation sent", incident.id)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_DEFAULT_RUNBOOK = """\
1. Check service logs for errors.
2. Perform an HTTP health check on affected endpoints.
3. If unhealthy, restart the service.
4. Verify recovery with another health check.
5. Call finish() with a summary.
"""
