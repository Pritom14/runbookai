"""AgentTraceRecorder — logs every agent action to the DB for incident replay.

This powers the "Incident Replay" UI (AgentTrace feature):
    GET /incidents/{id}/replay → full timeline of what the agent did

Every tool call, decision, and step is recorded with timing.
"""

import logging
import time
from contextlib import asynccontextmanager

logger = logging.getLogger("runbookai.trace")


class AgentTraceRecorder:
    """Records agent actions to the DB for post-incident replay.

    Usage:
        recorder = AgentTraceRecorder(db_session, incident_id)

        async with recorder.record("restart_service", {"host": "web-01", "service": "nginx"}):
            result = await restart_service("web-01", "nginx")
    """

    def __init__(self, session, incident_id: str):
        self.session = session
        self.incident_id = incident_id

    async def log_event(self, event: str, detail: dict) -> None:
        """Log a named lifecycle event (non-tool) to the AgentAction timeline.

        Uses tool_name="_event" so the replay UI can distinguish these from
        real tool calls and render them differently.
        """
        from runbookai.models import AgentAction

        action = AgentAction(
            incident_id=self.incident_id,
            tool_name="_event",
            tool_input={"event": event},
            tool_output=detail,
            duration_ms=0,
        )
        self.session.add(action)
        await self.session.commit()
        logger.info("trace: incident=%s event=%s", self.incident_id, event)

    @asynccontextmanager
    async def record(self, tool_name: str, tool_input: dict):
        """Context manager that times the tool call and writes an AgentAction row."""
        from runbookai.models import AgentAction

        start = time.monotonic()
        output = None
        try:
            yield lambda result: setattr(self, "_last_output", result)
            output = getattr(self, "_last_output", None)
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            action = AgentAction(
                incident_id=self.incident_id,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output=output,
                duration_ms=duration_ms,
            )
            self.session.add(action)
            await self.session.commit()
            logger.info(
                "trace: incident=%s tool=%s duration=%dms",
                self.incident_id,
                tool_name,
                duration_ms,
            )
