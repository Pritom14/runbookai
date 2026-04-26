"""Postmortem endpoint — auto-generate a blameless postmortem from AgentTrace data.

GET /incidents/{incident_id}/postmortem
    Returns a markdown document compiled from the incident's timeline, actions
    taken, outcome, and regression analysis.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import AgentAction, Incident

router = APIRouter(prefix="/incidents", tags=["postmortem"])


@router.get("/{incident_id}/postmortem")
async def get_postmortem(
    incident_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return an auto-generated blameless postmortem as markdown."""
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    result = await session.execute(
        select(AgentAction)
        .where(AgentAction.incident_id == incident_id)
        .order_by(AgentAction.created_at)
    )
    actions = result.scalars().all()

    markdown = _build_postmortem_markdown(incident, list(actions))
    return {
        "incident_id": incident_id,
        "markdown": markdown,
        "generated_at": datetime.utcnow(),
    }


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------


def _fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "n/a"
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _truncate(obj: Any, max_len: int = 200) -> str:
    if obj is None:
        return ""
    s = json.dumps(obj) if isinstance(obj, (dict, list)) else str(obj)
    return s[:max_len] + "..." if len(s) > max_len else s


def _build_postmortem_markdown(incident: Incident, actions: list[AgentAction]) -> str:
    alert_body = incident.alert_body or {}
    service = alert_body.get("service", "unknown")
    severity = alert_body.get("severity", "unknown")

    # Duration
    duration_str = "Ongoing"
    duration_secs: Optional[int] = None
    if incident.resolved_at and incident.created_at:
        duration_secs = int((incident.resolved_at - incident.created_at).total_seconds())
        duration_str = _fmt_duration(duration_secs)

    # Timeline base
    base_time = actions[0].created_at if actions else incident.created_at

    tool_actions = [a for a in actions if a.tool_name != "_event"]

    # --- Metadata table ---
    lines: list[str] = [
        f"# Postmortem: {incident.alert_name}",
        "",
        "## Metadata",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Incident ID | `{incident.id}` |",
        f"| Status | {incident.status} |",
        f"| Service | {service} |",
        f"| Severity | {severity} |",
        "| Started | "
        + (incident.created_at.strftime("%Y-%m-%d %H:%M UTC") if incident.created_at else "n/a")
        + " |",
        "| Resolved | "
        + (incident.resolved_at.strftime("%Y-%m-%d %H:%M UTC") if incident.resolved_at else "n/a")
        + " |",
        f"| Duration | {duration_str} |",
        "| Regression | "
        + (f"Yes — prior `{incident.prior_incident_id}`" if incident.possible_regression else "No")
        + " |",
        f"| Total Steps | {len(tool_actions)} |",
        "",
    ]

    # --- Summary ---
    lines += [
        "## Summary",
        "",
        incident.summary or "_No summary recorded. Agent may have escalated without resolving._",
        "",
    ]

    # --- Timeline ---
    lines += [
        "## Timeline",
        "",
        "| T+ | Event | Details | Duration |",
        "|----|-------|---------|----------|",
    ]
    for a in actions:
        t = int((a.created_at - base_time).total_seconds())
        dur = f"{a.duration_ms}ms" if a.duration_ms else "—"
        if a.tool_name == "_event":
            event_name = (a.tool_input or {}).get("event", "event")
            detail = _truncate(a.tool_output, 80)
            lines.append(f"| +{t}s | `{event_name}` | {detail} | {dur} |")
        else:
            inp = _truncate(a.tool_input, 80)
            out = _truncate(a.tool_output, 80)
            lines.append(f"| +{t}s | `{a.tool_name}` | input: {inp} → output: {out} | {dur} |")
    lines.append("")

    # --- Actions Taken ---
    lines += ["## Actions Taken", ""]
    if tool_actions:
        for a in tool_actions:
            t = int((a.created_at - base_time).total_seconds())
            out_summary = _truncate(a.tool_output, 120)
            lines.append(f"- **`{a.tool_name}`** at +{t}s — {out_summary}")
    else:
        lines.append("_No tool actions recorded._")
    lines.append("")

    # --- Regression Analysis ---
    lines += ["## Regression Analysis", ""]
    if incident.possible_regression:
        lines += [
            f"> **Warning:** This incident was flagged as a potential regression of "
            f"incident `{incident.prior_incident_id}`.",
            ">",
            "> The prior incident was recently remediated, but the same service failed again. "
            "The agent was instructed to investigate root cause rather than repeat the prior fix.",
            "",
        ]
    else:
        lines.append("No regression detected. This was a first-occurrence incident.")
    lines.append("")

    # --- Resolution ---
    lines += ["## Resolution", ""]
    if incident.status == "resolved":
        lines.append(f"Incident resolved automatically in {duration_str}.")
        lines.append(f"\n{incident.summary or ''}")
    elif incident.status == "escalated":
        lines.append("Incident escalated to on-call engineer. Automated resolution was not possible."  # noqa: E501
                     )
        lines.append(f"\nEscalation reason: {incident.summary or 'No reason recorded.'}")
    else:
        lines.append(f"Current status: **{incident.status}**. Incident may still be in progress.")
    lines.append("")

    # --- Recommendations placeholder ---
    lines += [
        "## Recommendations",
        "",
        "_To be filled in by the on-call engineer during the postmortem review._",
        "",
        "- [ ] Root cause identified and documented?",
        "- [ ] Runbook updated to reflect new learnings?",
        "- [ ] Follow-up tickets created for permanent fix?",
        "- [ ] Alert thresholds reviewed?",
        "",
        "---",
        "_Generated automatically by RunbookAI on "
        + datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        + "_",
    ]

    return "\n".join(lines)
