"""Slack notifications — rich Block Kit messages for incident lifecycle events.

Fires on: incident_started, approval_needed, approval_granted, approval_rejected,
           incident_resolved, incident_escalated.

Requires SLACK_WEBHOOK_URL in config. No-ops silently if not set.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from runbookai.config import settings

logger = logging.getLogger("runbookai.slack")


def _fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "n/a"
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def _build_blocks(
    event: str,
    incident: Any,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    extra = extra or {}
    alert = incident.alert_name
    inc_id = incident.id
    service = (incident.alert_body or {}).get("service", "unknown")
    severity = (incident.alert_body or {}).get("severity", "unknown")
    base_url = extra.get("base_url", "")
    replay_url = (
        f"{base_url}/incidents/{inc_id}/replay/ui"
        if base_url
        else f"/incidents/{inc_id}/replay/ui"
    )

    header_text: str
    color_emoji: str

    if event == "incident_started":
        color_emoji = ":rotating_light:"
        header_text = f"{color_emoji} *Incident Started* — {alert}"
        body = [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service:*\n{service}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n`{inc_id}`"},
                    {"type": "mrkdwn", "text": "*Status:*\nIn Progress"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Replay"},
                        "url": replay_url,
                    }
                ],
            },
        ]

    elif event == "approval_needed":
        color_emoji = ":warning:"
        tool = extra.get("tool", "unknown")
        rationale = extra.get("rationale", "")[:200]
        approval_id = extra.get("approval_id", "")
        header_text = f"{color_emoji} *Approval Required* — `{tool}`"
        body = [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident:*\n{alert}"},
                    {"type": "mrkdwn", "text": f"*Service:*\n{service}"},
                    {"type": "mrkdwn", "text": f"*Tool:*\n`{tool}`"},
                    {"type": "mrkdwn", "text": f"*Rationale:*\n{rationale}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*To approve:*\n"
                        f"```curl -X POST http://localhost:7000/approvals/{approval_id}/approve```"
                    ),
                },
            },
        ]

    elif event == "approval_granted":
        color_emoji = ":white_check_mark:"
        tool = extra.get("tool", "unknown")
        header_text = f"{color_emoji} *Approval Granted* — `{tool}` will execute"
        body = [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident:*\n{alert}"},
                    {"type": "mrkdwn", "text": f"*Tool:*\n`{tool}`"},
                ],
            }
        ]

    elif event == "approval_rejected":
        color_emoji = ":x:"
        tool = extra.get("tool", "unknown")
        reason = extra.get("reason", "No reason given")
        header_text = f"{color_emoji} *Approval Rejected* — `{tool}` will not execute"
        body = [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident:*\n{alert}"},
                    {"type": "mrkdwn", "text": f"*Reason:*\n{reason}"},
                ],
            }
        ]

    elif event == "incident_resolved":
        duration = None
        if incident.resolved_at and incident.created_at:
            duration = int((incident.resolved_at - incident.created_at).total_seconds())
        color_emoji = ":large_green_circle:"
        header_text = f"{color_emoji} *Incident Resolved* — {alert}"
        regression_note = (
            f"\n:warning: _Regression of incident `{incident.prior_incident_id}`_"
            if incident.possible_regression
            else ""
        )
        body = [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service:*\n{service}"},
                    {"type": "mrkdwn", "text": f"*Duration:*\n{_fmt_duration(duration)}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Summary:*\n{incident.summary or 'No summary.'}",
                    },
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n`{inc_id}`{regression_note}"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Postmortem"},
                        "url": f"{base_url}/incidents/{inc_id}/postmortem",
                    }
                ],
            },
        ]

    else:  # incident_escalated
        reason = extra.get("reason", "No reason given")
        color_emoji = ":red_circle:"
        header_text = f"{color_emoji} *Incident Escalated* — {alert}"
        body = [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service:*\n{service}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
                    {"type": "mrkdwn", "text": f"*Reason:*\n{reason[:300]}"},
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n`{inc_id}`"},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Replay"},
                        "url": replay_url,
                    }
                ],
            },
        ]

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
        {"type": "divider"},
        *body,
    ]
    return blocks


async def send_slack_notification(
    event: str,
    incident: Any,
    extra: dict[str, Any] | None = None,
) -> None:
    """Post a Slack Block Kit message for an incident lifecycle event.

    No-ops if SLACK_WEBHOOK_URL is not configured.
    Never raises — Slack failures must not break the agent loop.
    """
    if not settings.slack_webhook_url:
        return
    try:
        blocks = _build_blocks(event, incident, extra)
        payload = {"blocks": blocks}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.slack_webhook_url, json=payload)
            if resp.status_code != 200:
                logger.warning(
                    "slack notification returned %s: %s", resp.status_code, resp.text[:200]
                )
            else:
                logger.info("slack notification sent: event=%s incident=%s", event, incident.id)
    except Exception:
        logger.exception(
            "failed to send slack notification: event=%s incident=%s", event, incident.id
        )
