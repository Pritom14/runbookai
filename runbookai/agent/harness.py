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
from pathlib import Path
from typing import Any, Optional

from runbookai.agent.suggest_mode import RESOLVED, SuggestModeAgent
from runbookai.config import settings
from runbookai.models import IncidentStatus
from runbookai.slack import send_slack_notification
from runbookai.trace.recorder import AgentTraceRecorder

# Import ExperienceStore for Phase 2A
try:
    from soma_memory.experience import ExperienceStore
    _SOMA_AVAILABLE = True
except ImportError:
    _SOMA_AVAILABLE = False

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
    escalation_reason: Optional[str] = None


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
        suggest_mode: Optional[bool] = None,
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

        recorder = AgentTraceRecorder(session, self.incident_id)

        # Phase 2A: Retrieve similar past experiences from soma-memory.
        similar_experiences = []
        if _SOMA_AVAILABLE:
            try:
                store = ExperienceStore()
                # Query using alert name and service as context for similarity search
                query_context = f"{incident.alert_name} {alert_context.get('service', 'unknown')}"
                similar_experiences = store.find_similar(
                    context=query_context,
                    domain="incident_response",
                    limit=3
                )
                if similar_experiences:
                    experience_summaries = [
                        {
                            "id": e.id,
                            "action": e.action,
                            "success": e.success,
                            "confidence": e.confidence,
                        }
                        for e in similar_experiences
                    ]
                    await recorder.log_event(
                        "used_past_experience",
                        {
                            "count": len(similar_experiences),
                            "experiences": experience_summaries,
                        },
                    )
                    logger.info(
                        "incident=%s retrieved %d similar experiences",
                        self.incident_id,
                        len(similar_experiences),
                    )
            except Exception as e:
                logger.warning("failed to retrieve experiences from soma-memory: %s", e)

        # Fetch the matching runbook (optional in Phase 2B).
        runbook_text = await self._load_runbook(incident.alert_name, session=session)
        await recorder.log_event(
            "runbook_matched",
            {"alert_name": incident.alert_name, "runbook_preview": runbook_text[:200]},
        )

        # Build initial context dict passed to propose_next_action on step 0.
        context: dict[str, Any] = {
            "alert": alert_context,
            "runbook": runbook_text,
            "previous_actions": [],
            "experiences": similar_experiences,  # Phase 2A: inject experiences
        }

        # Inject regression context if this incident was flagged.
        if incident.possible_regression and incident.prior_incident_id:
            prior = await self._load_incident_by_id(incident.prior_incident_id, session)
            if prior:
                minutes_ago = int(
                    (datetime.utcnow() - prior.created_at).total_seconds() / 60
                )
                context["regression"] = {
                    "prior_incident_id": prior.id,
                    "prior_summary": prior.summary or "No summary recorded.",
                    "minutes_ago": minutes_ago,
                }
                await recorder.log_event(
                    "regression_detected",
                    {
                        "prior_incident_id": prior.id,
                        "minutes_ago": minutes_ago,
                        "prior_summary": prior.summary,
                    },
                )

        # Create (or retrieve existing) agent for this incident.
        agent = _ACTIVE_AGENTS.get(self.incident_id)
        if agent is None:
            agent = await SuggestModeAgent.create(self.incident_id, session)
            _ACTIVE_AGENTS[self.incident_id] = agent

        # Update incident status to in_progress.
        incident.status = IncidentStatus.IN_PROGRESS
        await session.commit()
        await send_slack_notification("incident_started", incident)

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
                await recorder.log_event("resolved", {"step": step + 1})

                # Write back to PagerDuty if this incident came from PagerDuty
                await self._writeback_pagerduty(session, incident, resolution_summary, recorder)

                # Write back to Datadog if this incident came from Datadog
                await self._writeback_datadog(session, incident, resolution_summary, recorder)

                # Phase 2C: Write-back loop — record incident as experience for future learning.
                await self._record_experience(
                    session,
                    recorder,
                    incident,
                    actions_taken,
                    success=True,
                )

                await send_slack_notification("incident_resolved", incident)
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
                await recorder.log_event(
                    "approval_requested",
                    {
                        "tool": action.tool_name,
                        "approval_id": approval_id,
                        "rationale": action.rationale,
                    },
                )
                await send_slack_notification(
                    "approval_needed",
                    incident,
                    {
                        "tool": action.tool_name,
                        "approval_id": approval_id,
                        "rationale": action.rationale,
                    },
                )
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

            # Guard: Check if agent read critical thermal sensor but didn't call fan_override
            await self._check_thermal_remediation_guard(
                session, incident, action, result, actions_taken, recorder
            )

            # Exit early if thermal guard escalated
            if hasattr(incident, "_force_exit") and incident._force_exit:
                _ACTIVE_AGENTS.pop(self.incident_id, None)
                return IncidentResult(
                    incident_id=self.incident_id,
                    resolved=False,
                    summary=incident.summary,
                    actions_taken=actions_taken,
                    escalation_reason=incident.summary,
                )

        # MAX_STEPS exhausted without resolution.
        escalation_reason = f"Reached MAX_STEPS ({self.MAX_STEPS}) without resolving incident."
        await self._escalate(session, incident, escalation_reason, recorder)

        # Phase 2C: Record escalation as failed experience for learning.
        await self._record_experience(
            session,
            recorder,
            incident,
            actions_taken,
            success=False,
        )

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
        from runbookai.models import Incident

        incident = await session.get(Incident, self.incident_id)
        if incident is None:
            raise ValueError(f"Incident {self.incident_id} not found")
        return incident

    async def _load_incident_by_id(self, incident_id: str, session: Any) -> Any:
        from runbookai.models import Incident

        return await session.get(Incident, incident_id)

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

    async def _escalate(
        self, session: Any, incident: Any, reason: str, recorder: Optional[AgentTraceRecorder] = None
    ) -> None:
        """Mark incident as escalated and send an email notification if configured."""
        incident.status = IncidentStatus.ESCALATED
        incident.summary = reason
        await session.commit()
        if recorder:
            await recorder.log_event("escalated", {"reason": reason})
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

        await send_slack_notification("incident_escalated", incident, {"reason": reason})

    async def _record_experience(
        self,
        session: Any,
        recorder: AgentTraceRecorder,
        incident: Any,
        actions_taken: list[dict[str, Any]],
        success: bool,
    ) -> None:
        """Phase 2C: Record incident resolution (or failure) to soma-memory.

        After every incident (resolved or escalated), write an experience entry
        so the agent learns from production. Over time, repeated patterns
        crystallize into beliefs.
        """
        if not _SOMA_AVAILABLE:
            logger.info("incident=%s soma-memory not available, skipping experience record", self.incident_id)
            return

        try:
            from soma_memory.experience import ExperienceStore

            store = ExperienceStore()

            # Build context: alert name + service + severity for similarity search.
            context = f"{incident.alert_name}"
            if incident.alert_body.get("service"):
                context += f" service={incident.alert_body['service']}"
            if incident.alert_body.get("severity"):
                context += f" severity={incident.alert_body['severity']}"

            # Build action summary: tool sequence that was taken.
            action_sequence = " → ".join(
                [a["tool_name"] for a in actions_taken if a["tool_name"] != "_event"]
            )
            if not action_sequence:
                action_sequence = "no tools called"

            # Store the experience.
            store.record(
                domain="incident_response",
                context=context,
                action=action_sequence,
                outcome=incident.summary or "No summary recorded",
                success=success,
                model_used=settings.llm_model,
                notes=f"incident_id={incident.id} steps={len(actions_taken)}",
            )

            await recorder.log_event(
                "experience_saved",
                {
                    "context": context,
                    "action_sequence": action_sequence,
                    "success": success,
                },
            )
            logger.info(
                "incident=%s recorded experience: success=%s actions=%s",
                self.incident_id,
                success,
                action_sequence,
            )
        except Exception as e:
            logger.warning("incident=%s failed to record experience: %s", self.incident_id, e)

    async def _writeback_pagerduty(
        self,
        session: Any,
        incident: Any,
        resolution_summary: str,
        recorder: Optional[AgentTraceRecorder] = None,
    ) -> None:
        """Write back incident resolution to PagerDuty API if applicable.

        Only writes back if:
        1. incident.source == "pagerduty"
        2. PAGERDUTY_API_KEY is configured
        3. The incident's alert_body contains a PagerDuty incident ID

        Logs success/failure to audit trail.
        """
        if incident.source != "pagerduty":
            return

        if not settings.pagerduty_api_key:
            logger.warning("incident=%s PAGERDUTY_API_KEY not configured", self.incident_id)
            return

        # Extract incident ID from alert_body
        # PagerDuty v3 webhook puts it in event.data.incident.id
        alert_body = incident.alert_body or {}
        event = alert_body.get("event", {})
        data = event.get("data", {})
        incident_data = data.get("incident", alert_body)
        pd_incident_id = incident_data.get("id")

        if not pd_incident_id:
            logger.warning(
                "incident=%s PagerDuty incident ID not found in alert body",
                self.incident_id,
            )
            return

        # Call PagerDuty API to resolve the incident
        from runbookai.integrations.pagerduty import resolve_incident

        pd_result = await resolve_incident(
            pd_incident_id,
            settings.pagerduty_api_key,
            resolution_summary=resolution_summary,
        )

        if pd_result["success"]:
            logger.info(
                "incident=%s PagerDuty write-back successful: %s",
                self.incident_id,
                pd_result["message"],
            )
            if recorder:
                await recorder.log_event(
                    "pagerduty_writeback",
                    {
                        "pd_incident_id": pd_incident_id,
                        "status": "success",
                        "message": pd_result["message"],
                    },
                )
        else:
            logger.error(
                "incident=%s PagerDuty write-back failed: %s",
                self.incident_id,
                pd_result["message"],
            )
            if recorder:
                await recorder.log_event(
                    "pagerduty_writeback",
                    {
                        "pd_incident_id": pd_incident_id,
                        "status": "failed",
                        "error": pd_result["message"],
                    },
                )

    async def _writeback_datadog(
        self,
        session: Any,
        incident: Any,
        resolution_summary: str,
        recorder: Optional[AgentTraceRecorder] = None,
    ) -> None:
        """Write back incident resolution to Datadog Events API if applicable.

        Only writes back if:
        1. incident.source == "datadog"
        2. DATADOG_API_KEY is configured
        3. The incident's alert_body contains required Datadog data

        Logs success/failure to audit trail.
        """
        if incident.source != "datadog":
            return

        if not settings.datadog_api_key:
            logger.warning("incident=%s DATADOG_API_KEY not configured", self.incident_id)
            return

        # Extract monitor ID from alert_body
        alert_body = incident.alert_body or {}
        dd_monitor_id = str(alert_body.get("id", ""))

        if not dd_monitor_id:
            logger.warning(
                "incident=%s Datadog monitor ID not found in alert body",
                self.incident_id,
            )
            return

        # Post resolution event to Datadog
        from runbookai.integrations.datadog import post_event

        event_title = f"Incident Resolved: {incident.alert_name}"
        event_text = f"RunbookAI resolved incident for monitor {dd_monitor_id}.\n\nSummary: {resolution_summary}"
        tags = [
            f"runbookai:incident_id:{self.incident_id}",
            f"datadog:monitor_id:{dd_monitor_id}",
        ]

        dd_result = await post_event(
            event_title,
            settings.datadog_api_key,
            site=settings.datadog_site or "datadoghq.com",
            alert_type="success",
            text=event_text,
            tags=tags,
        )

        if dd_result["success"]:
            logger.info(
                "incident=%s Datadog write-back successful: %s",
                self.incident_id,
                dd_result["message"],
            )
            if recorder:
                await recorder.log_event(
                    "datadog_writeback",
                    {
                        "dd_monitor_id": dd_monitor_id,
                        "status": "success",
                        "message": dd_result["message"],
                    },
                )
        else:
            logger.error(
                "incident=%s Datadog write-back failed: %s",
                self.incident_id,
                dd_result["message"],
            )
            if recorder:
                await recorder.log_event(
                    "datadog_writeback",
                    {
                        "dd_monitor_id": dd_monitor_id,
                        "status": "failed",
                        "error": dd_result["message"],
                    },
                )

    async def _check_thermal_remediation_guard(
        self,
        session: Any,
        incident: Any,
        action: Any,
        result: dict[str, Any],
        actions_taken: list[dict[str, Any]],
        recorder: AgentTraceRecorder,
    ) -> None:
        """Guard: track critical thermal state and force fan_override if agent skips it.

        Pattern: If read_bmc_sensors finds any_critical=True, the NEXT action
        MUST be fan_override. If the agent proposes something else, we escalate
        with an error: "Agent did not execute critical remediation (fan_override)
        despite critical thermal state."
        """
        # Track critical thermal state from read_bmc_sensors calls
        if action.tool_name == "read_bmc_sensors":
            if result.get("any_critical", False):
                critical_alerts = result.get("critical_alerts", [])
                logger.warning(
                    "incident=%s read_bmc_sensors detected CRITICAL: %s",
                    self.incident_id,
                    critical_alerts,
                )
                # Store state in incident for the next iteration check
                incident._thermal_critical_pending = True
                incident._critical_alerts = critical_alerts

        # Check if we were waiting for fan_override but got something else
        elif hasattr(incident, "_thermal_critical_pending") and incident._thermal_critical_pending:
            if action.tool_name != "fan_override":
                escalation_msg = (
                    f"Agent did not execute critical remediation. "
                    f"Critical thermal state detected ({incident._critical_alerts}) "
                    f"but agent proposed '{action.tool_name}' instead of 'fan_override'."
                )
                logger.error("incident=%s %s", self.incident_id, escalation_msg)
                await self._escalate(session, incident, escalation_msg, recorder)
                # Force incident into escalated state to prevent further loops
                incident._force_exit = True
            else:
                # Agent correctly called fan_override — clear the flag
                incident._thermal_critical_pending = False


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

