"""Incident analysis — cross-incident patterns, regression history, and diffs.

Endpoints:
  GET /incidents/analysis          — service-level pattern summary
  GET /incidents/compare           — side-by-side diff of two incidents
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import AgentAction, Incident

router = APIRouter(prefix="/incidents", tags=["analysis"])

_REMEDIATION_TOOLS = {"restart_service", "clear_disk", "scale_service"}


@router.get("/analysis")
async def incident_analysis(
    hours: int = Query(default=24, description="Look-back window in hours"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return a pattern summary across all recent incidents.

    Groups incidents by service, detects regressions, calculates MTTR,
    and surfaces the most commonly used tools.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = await session.execute(
        select(Incident)
        .where(Incident.created_at >= cutoff)
        .order_by(Incident.created_at.asc())
    )
    incidents = result.scalars().all()

    # Group by service
    by_service: dict[str, list[Incident]] = defaultdict(list)
    for inc in incidents:
        service = inc.alert_body.get("service", "unknown") if inc.alert_body else "unknown"
        by_service[service].append(inc)

    service_summaries = []
    for service, incs in by_service.items():
        resolved = [i for i in incs if i.status == "resolved"]
        regressions = [i for i in incs if i.possible_regression]
        mttr_seconds = None
        if resolved:
            durations = [
                int((i.resolved_at - i.created_at).total_seconds())
                for i in resolved
                if i.resolved_at
            ]
            if durations:
                mttr_seconds = int(sum(durations) / len(durations))

        # Most common tools across this service's incidents
        tool_counts: dict[str, int] = defaultdict(int)
        for inc in incs:
            actions_result = await session.execute(
                select(AgentAction).where(AgentAction.incident_id == inc.id)
            )
            for action in actions_result.scalars().all():
                if action.tool_name != "_event":
                    tool_counts[action.tool_name] += 1

        service_summaries.append({
            "service": service,
            "total_incidents": len(incs),
            "resolved": len(resolved),
            "escalated": len([i for i in incs if i.status == "escalated"]),
            "regressions": len(regressions),
            "avg_mttr_seconds": mttr_seconds,
            "top_tools": sorted(tool_counts.items(), key=lambda x: -x[1])[:5],
            "incidents": [
                {
                    "id": i.id,
                    "alert_name": i.alert_name,
                    "status": i.status,
                    "possible_regression": i.possible_regression,
                    "prior_incident_id": i.prior_incident_id,
                    "created_at": i.created_at,
                    "resolved_at": i.resolved_at,
                }
                for i in incs
            ],
        })

    total = len(incidents)
    resolved_total = len([i for i in incidents if i.status == "resolved"])
    regression_total = len([i for i in incidents if i.possible_regression])

    return {
        "window_hours": hours,
        "total_incidents": total,
        "resolved": resolved_total,
        "escalated": len([i for i in incidents if i.status == "escalated"]),
        "regressions_detected": regression_total,
        "auto_resolution_rate_pct": (
            round(resolved_total / total * 100, 1) if total else 0
        ),
        "by_service": service_summaries,
    }


@router.get("/compare")
async def compare_incidents(
    incident_a: str = Query(..., description="First incident ID"),
    incident_b: str = Query(..., description="Second incident ID"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Side-by-side diff of two incident traces.

    Shows what tools each incident used, how outcomes differed,
    whether incident B is a regression of incident A, and key
    differences in tool outputs (latency, connection counts, etc).
    """
    inc_a = await session.get(Incident, incident_a)
    inc_b = await session.get(Incident, incident_b)

    if not inc_a or not inc_b:
        missing = incident_a if not inc_a else incident_b
        return {"error": f"Incident {missing} not found"}

    async def get_trace(inc: Incident) -> list[dict[str, Any]]:
        result = await session.execute(
            select(AgentAction)
            .where(AgentAction.incident_id == inc.id)
            .order_by(AgentAction.created_at)
        )
        actions = result.scalars().all()
        base = actions[0].created_at if actions else inc.created_at
        return [
            {
                "t_seconds": int((a.created_at - base).total_seconds()),
                "tool": a.tool_name,
                "input": a.tool_input,
                "output": a.tool_output,
                "duration_ms": a.duration_ms,
            }
            for a in actions
            if a.tool_name != "_event"
        ]

    trace_a = await get_trace(inc_a)
    trace_b = await get_trace(inc_b)

    tools_a = {s["tool"] for s in trace_a}
    tools_b = {s["tool"] for s in trace_b}

    # Extract key metrics from tool outputs for comparison
    def extract_metrics(trace: list[dict]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        for step in trace:
            tool = step["tool"]
            out = step.get("output") or {}
            if tool == "http_check":
                metrics["http_latency_ms"] = out.get("latency_ms")
                metrics["http_healthy"] = out.get("healthy")
            elif tool == "run_db_check":
                raw = out.get("raw", "")
                # Pull first data row from psql output
                for line in raw.splitlines():
                    line = line.strip()
                    if line and line[0].isdigit():
                        metrics["db_row"] = line
                        break
            elif tool == "query_metrics":
                metrics["cpu_pct"] = out.get("cpu_used_pct")
                mem = out.get("memory_mb", {})
                metrics["mem_used_mb"] = mem.get("used_mb")
            elif tool == "check_disk":
                critical = out.get("critical_mounts", [])
                metrics["critical_mounts"] = [m["mount"] for m in critical]
        return metrics

    metrics_a = extract_metrics(trace_a)
    metrics_b = extract_metrics(trace_b)

    # Compute time between incidents
    gap_minutes = int((inc_b.created_at - inc_a.created_at).total_seconds() / 60)

    is_regression = (
        inc_b.possible_regression and inc_b.prior_incident_id == incident_a
    )

    def mttr(inc: Incident) -> int | None:
        if inc.resolved_at:
            return int((inc.resolved_at - inc.created_at).total_seconds())
        return None

    return {
        "incident_a": {
            "id": inc_a.id,
            "alert_name": inc_a.alert_name,
            "status": inc_a.status,
            "created_at": inc_a.created_at,
            "mttr_seconds": mttr(inc_a),
            "summary": inc_a.summary,
            "tools_used": sorted(tools_a),
            "metrics": metrics_a,
            "steps": len(trace_a),
        },
        "incident_b": {
            "id": inc_b.id,
            "alert_name": inc_b.alert_name,
            "status": inc_b.status,
            "created_at": inc_b.created_at,
            "mttr_seconds": mttr(inc_b),
            "summary": inc_b.summary,
            "tools_used": sorted(tools_b),
            "metrics": metrics_b,
            "steps": len(trace_b),
            "possible_regression": inc_b.possible_regression,
        },
        "diff": {
            "gap_minutes": gap_minutes,
            "is_regression": is_regression,
            "tools_added_in_b": sorted(tools_b - tools_a),
            "tools_dropped_in_b": sorted(tools_a - tools_b),
            "outcome_changed": inc_a.status != inc_b.status,
            "metric_changes": {
                k: {"a": metrics_a.get(k), "b": metrics_b.get(k)}
                for k in set(metrics_a) | set(metrics_b)
                if metrics_a.get(k) != metrics_b.get(k)
            },
        },
    }
