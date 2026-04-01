"""AgentHarness — top-level controller that runs an incident response session.

Coordinates:
- Alert intake → runbook lookup
- LLM decision loop (Suggest Mode or Autonomous)
- Tool execution
- AgentTrace recording
- Escalation if unresolved
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("runbookai.harness")


@dataclass
class IncidentResult:
    incident_id: str
    resolved: bool
    summary: str
    actions_taken: list[dict] = field(default_factory=list)
    escalation_reason: str | None = None


class AgentHarness:
    """Runs a full incident response session for a given incident.

    Usage:
        harness = AgentHarness(incident_id="...", suggest_mode=True)
        result = await harness.run()

    TODO:
    - Load incident from DB
    - Fetch matching runbook (from DB or GitHub)
    - Enter decision loop:
        suggest_mode=True  → SuggestModeAgent (propose + wait for approval)
        suggest_mode=False → AutonomousAgent (execute directly, still logs everything)
    - After each action, check resolution condition
    - If max_steps reached without resolution → escalate
    """

    MAX_STEPS = 10

    def __init__(self, incident_id: str, suggest_mode: bool = True):
        self.incident_id = incident_id
        self.suggest_mode = suggest_mode

    async def run(self) -> IncidentResult:
        """Entry point — run the full incident response loop.

        TODO: implement full loop. See class docstring.
        """
        raise NotImplementedError("TODO: implement agent decision loop")

    async def _load_runbook(self, alert_name: str) -> str:
        """Fetch the runbook for this alert type.

        TODO: Look up from local DB first, fall back to GitHub repo.
        Returns runbook text (markdown).
        """
        raise NotImplementedError("TODO: implement runbook lookup")

    async def _escalate(self, reason: str) -> None:
        """Mark incident as escalated, notify on-call human.

        TODO: Call PagerDuty API to reassign / post to Slack.
        """
        raise NotImplementedError("TODO: implement escalation")
