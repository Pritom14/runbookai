#!/usr/bin/env python3
"""RunbookAI automated demo runner.

Fires a demo alert, watches the agent work in real time, auto-approves
high-risk actions with a visible countdown, and prints the full AgentTrace
timeline at the end.

Usage:
    # Make sure RunbookAI is running first:
    DEMO_MODE=true uvicorn runbookai.main:app --port 7000

    # Then in another terminal:
    python demo/run_demo.py checkout    # DB connection leak → escalates with root cause
    python demo/run_demo.py payment     # OOM crash → restarts → recovers
    python demo/run_demo.py disk        # Disk full → clears → verifies

Options:
    --no-approve    Show the approval pause but don't auto-approve (for live demos)
    --url URL       RunbookAI base URL (default: http://localhost:7000)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
WHITE = "\033[37m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


def hr(char: str = "─", width: int = 70) -> None:
    print(c(DIM, char * width))


def section(title: str) -> None:
    print()
    hr()
    print(f"  {c(BOLD, title)}")
    hr()


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no requests dep)
# ---------------------------------------------------------------------------

def _request(method: str, url: str, body: dict | None = None, timeout: int = 10) -> dict:
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(), "status_code": e.code}


def get(base: str, path: str) -> dict:
    return _request("GET", f"{base}{path}")


def post(base: str, path: str, body: dict | None = None, timeout: int = 10) -> dict:
    return _request("POST", f"{base}{path}", body, timeout=timeout)


# ---------------------------------------------------------------------------
# Alert payloads
# ---------------------------------------------------------------------------

ALERTS: dict[str, dict] = {
    "checkout": {
        "alert_name": "checkout service p99 latency high",
        "severity": "high",
        "service": "checkout-service",
        "details": "p99 > 4s for 10 minutes. 12% error rate. On-call paged.",
        "host": "ssh-target",
    },
    "payment": {
        "alert_name": "payment-service 503 rate spike",
        "severity": "critical",
        "service": "payment-service",
        "details": "503 error rate at 47% for 5 minutes. On-call paged.",
        "host": "ssh-target",
    },
    "disk": {
        "alert_name": "disk usage critical on web-01",
        "severity": "high",
        "service": "web-01",
        "details": "/var/log at 94% capacity. Write errors imminent.",
        "host": "ssh-target",
    },
    # regression fires checkout TWICE — second alert is auto-detected as regression
    "regression": {
        "alert_name": "checkout service p99 latency high",
        "severity": "high",
        "service": "checkout-service",
        "details": "p99 > 4s for 10 minutes. 12% error rate. On-call paged.",
        "host": "ssh-target",
    },
}

SCENARIO_LABELS = {
    "checkout":   "checkout-latency  —  DB connection leak → escalate with root cause",
    "payment":    "payment-service-503  —  OOM crash → restart → recover",
    "disk":       "disk-full  —  /var/log 94% → clear old logs → verify",
    "regression": (
        "regression demo  —  same service alerts twice"
        " → agent detects pattern, escalates smarter"
    ),
}

# ---------------------------------------------------------------------------
# Tool output formatters
# ---------------------------------------------------------------------------

TOOL_ICONS = {
    "http_check":        "[http]  ",
    "check_logs":        "[logs]  ",
    "check_disk":        "[disk]  ",
    "check_processes":   "[proc]  ",
    "query_metrics":     "[metr]  ",
    "run_db_check":      "[db]    ",
    "restart_service":   "[restart]",
    "clear_disk":        "[clear] ",
    "ssh_execute":       "[ssh]   ",
    "scale_service":     "[scale] ",
    "finish":            "[done]  ",
    "_event":            ">>",
}

HIGH_RISK = {"restart_service", "clear_disk", "scale_service", "ssh_execute"}


def fmt_tool_output(tool: str, output: dict | None) -> str:
    if not output:
        return ""
    lines = []
    if tool == "http_check":
        healthy = output.get("healthy")
        status_code = output.get("status_code", "?")
        latency = output.get("latency_ms", "?")
        mark = c(GREEN, "HEALTHY") if healthy else c(RED, "UNHEALTHY")
        lines.append(f"    {mark}  HTTP {status_code}  latency={latency}ms")
    elif tool == "check_disk":
        for m in output.get("critical_mounts", []):
            lines.append(f"    {c(RED, '! CRITICAL')} {m['mount']}  {m['used_pct']} used")
        for m in output.get("mounts", []):
            if m not in output.get("critical_mounts", []):
                lines.append(f"    {c(DIM, m['mount'])}  {m['used_pct']} used")
    elif tool == "check_processes":
        running = output.get("running")
        mark = c(GREEN, "RUNNING") if running else c(RED, "NOT RUNNING")
        lines.append(f"    {mark}  count={output.get('count', 0)}")
        for p in output.get("processes", [])[:2]:
            proc_info = f"pid={p['pid']}  cpu={p['cpu_pct']}%  mem={p['mem_pct']}%"
            lines.append(f"    {c(DIM, proc_info)}")
    elif tool == "query_metrics":
        cpu = output.get("cpu_used_pct")
        mem = output.get("memory_mb", {})
        load = output.get("load_average", {})
        lines.append(f"    CPU {cpu}%  |  "
                     f"mem {mem.get('used_mb', '?')}/{mem.get('total_mb', '?')} MB  |  "
                     f"load {load.get('1m', '?')}")
    elif tool == "run_db_check":
        raw = output.get("raw", "")
        # Extract the numbers row
        for line in raw.splitlines():
            line = line.strip()
            if line and line[0].isdigit():
                lines.append(f"    {c(YELLOW, line)}")
                break
        lines.append(f"    {c(DIM, '(total | active | idle_in_tx | longest_query_s)')}")
    elif tool == "check_logs":
        stdout = output.get("stdout", "")
        # Show last 4 lines
        log_lines = [ln for ln in stdout.strip().splitlines() if ln][-4:]
        for ll in log_lines:
            color = RED if "ERROR" in ll or "FATAL" in ll else (YELLOW if "WARN" in ll else DIM)
            lines.append(f"    {c(color, ll[:90])}")
    elif tool == "restart_service":
        active = output.get("active")
        mark = c(GREEN, "active") if active else c(RED, "FAILED")
        lines.append(f"    service is now: {mark}")
    elif tool == "clear_disk":
        deleted = output.get("files_deleted", "?")
        path = output.get("path", "?")
        lines.append(f"    deleted {deleted} files from {path}")
    elif tool == "_event":
        event = output.get("tool_input", {}).get("event") or output.get("event", "")
        detail = output.get("tool_output") or {}
        lines.append(f"    {c(MAGENTA, event)}  {c(DIM, str(detail)[:80])}")
    return "\n".join(lines)


def print_step(step: dict, idx: int) -> None:
    tool = step.get("tool", "")
    t = step.get("t_seconds", 0)
    duration = step.get("duration_ms")
    icon = TOOL_ICONS.get(tool, "•")
    output = step.get("output") or {}

    if tool == "_event":
        event_name = output.get("tool_input", {}).get("event", "event")
        print(f"  {c(MAGENTA, f'>> {event_name}')}")
        return

    risk_label = c(RED, " [HIGH-RISK]") if tool in HIGH_RISK else ""
    dur_label = c(DIM, f"  {duration}ms") if duration else ""
    print(f"  {c(DIM, f't+{t:>3}s')}  {icon} {c(BOLD, tool)}{risk_label}{dur_label}")
    formatted = fmt_tool_output(tool, output)
    if formatted:
        print(formatted)


# ---------------------------------------------------------------------------
# Polling + auto-approve
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"resolved", "escalated"}
APPROVAL_COUNTDOWN = 5


def poll_until_done(base: str, incident_id: str, auto_approve: bool) -> dict:
    print()
    print(c(CYAN, f"  Incident ID: {incident_id}"))
    print(c(DIM, f"  Replay UI:  {base}/incidents/{incident_id}/replay/ui"))
    print()

    seen_actions: set[str] = set()
    step_idx = 0

    while True:
        time.sleep(3)
        incident = get(base, f"/incidents/{incident_id}")
        status = incident.get("status", "unknown")

        # Fetch and print any new trace steps
        replay = get(base, f"/incidents/{incident_id}/replay")
        for step in replay.get("timeline", []):
            key = f"{step.get('tool')}_{step.get('t_seconds')}"
            if key not in seen_actions:
                seen_actions.add(key)
                print_step(step, step_idx)
                step_idx += 1

        if status in TERMINAL_STATUSES:
            return incident

        if status == "waiting_approval":
            handle_approval(base, incident_id, auto_approve)

        # Continue polling


def handle_approval(base: str, incident_id: str, auto_approve: bool) -> None:
    pending = get(base, "/approvals/pending")
    approvals = [
        a for a in pending.get("approvals", [])
        if a.get("incident_id") == incident_id
    ]
    if not approvals:
        return

    approval = approvals[0]
    approval_id = approval["id"]
    tool_name = approval["tool_name"]
    rationale = approval.get("rationale", "")

    print()
    hr("━")
    print(f"  {c(YELLOW, '!  APPROVAL REQUIRED')}")
    print(f"  Tool:      {c(BOLD, tool_name)}")
    print(f"  Rationale: {c(DIM, rationale[:120])}")
    hr("━")

    if not auto_approve:
        print(f"  {c(DIM, 'Run: curl -X POST ' + base + '/approvals/' + approval_id + '/approve')}")
        print(f"  {c(DIM, 'Waiting for manual approval...')}")
        # Poll until approved externally
        while True:
            time.sleep(2)
            incident = get(base, f"/incidents/{incident_id}")
            if incident.get("status") != "waiting_approval":
                return
        return

    # Auto-approve with countdown
    for i in range(APPROVAL_COUNTDOWN, 0, -1):
        print(f"\r  {c(YELLOW, f'Auto-approving in {i}s...')} ", end="", flush=True)
        time.sleep(1)
    print(f"\r  {c(GREEN, 'Approving...')}              ")

    # LLM can take 60-120s on a local model — use a generous timeout.
    result = post(base, f"/approvals/{approval_id}/approve", timeout=180)
    if "error" in result:
        print(c(RED, f"  Approval failed: {result['error']}"))
    else:
        print(c(GREEN, "  ✓ Approved — agent continuing"))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_competitor_table() -> None:
    """Print a side-by-side comparison with incumbent tools."""
    section("RUNBOOKAI vs THE INCUMBENTS")

    col = 22
    tools = ["PagerDuty", "OpsGenie", "incident.io", "Grafana OnCall", "RunbookAI"]
    features = [
        ("Automated diagnosis", [False, False, False, False, True]),
        ("Executes remediation",  [False, False, False, False, True]),
        ("Regression detection",  [False, False, False, False, True]),
        ("Full AgentTrace replay", [False, False, False, False, True]),
        ("Suggest Mode (human-in-loop)", [False, False, True, False, True]),
        ("Self-hosted / open-source",    [False, False, False, False, True]),
        ("One-command install",           [False, False, False, False, True]),
    ]

    header = "  " + "Feature".ljust(32)
    for t in tools:
        header += t.ljust(col)
    print(c(BOLD, header))
    hr()

    for feature, values in features:
        row = "  " + feature.ljust(32)
        for i, val in enumerate(values):
            if i == len(tools) - 1:  # RunbookAI column
                mark = c(GREEN, "✓  YES".ljust(col))
            elif val:
                mark = c(CYAN, "✓  YES".ljust(col))
            else:
                mark = c(DIM, "✗  No ".ljust(col))
            row += mark
        print(row)

    print()
    print(c(DIM, "  Incumbents page humans. RunbookAI is the human."))
    print()


def run_single_scenario(
    base: str, scenario: str, auto_approve: bool, label_prefix: str = ""
) -> tuple[str, dict]:
    """Fire one alert, wait until done. Returns (incident_id, incident_dict)."""
    alert = ALERTS[scenario]
    label = label_prefix or SCENARIO_LABELS[scenario]
    section(f"SCENARIO: {label}")

    print(f"  {c(RED, 'ALERT RECEIVED')}")
    print(f"  {c(BOLD, alert['alert_name'])}")
    print(f"  {c(DIM, alert['details'])}")
    print()

    print(c(DIM, "  → Sending to RunbookAI..."))
    resp = post(base, "/webhooks/generic", alert)
    if resp.get("possible_regression"):
        prior = resp.get("prior_incident_id")
        print(c(YELLOW, f"  !  REGRESSION DETECTED — prior incident {prior}"))
    incident_id = resp.get("incident_id")
    if not incident_id:
        print(c(RED, f"  ✗  Failed to create incident: {resp}"))
        sys.exit(1)

    section("AGENT WORKING")
    t_start = time.monotonic()
    incident = poll_until_done(base, incident_id, auto_approve)
    elapsed = time.monotonic() - t_start

    status = incident.get("status", "unknown")
    summary = incident.get("summary", "No summary.")

    section("RESULT")
    if status == "resolved":
        print(f"  {c(GREEN, '✓  RESOLVED')}  in {elapsed:.0f}s")
    elif status == "escalated":
        print(f"  {c(YELLOW, '⚡ ESCALATED')}  in {elapsed:.0f}s")
    else:
        print(f"  {c(DIM, status)}  in {elapsed:.0f}s")

    print()
    print(c(BOLD, "  Summary:"))
    for line in (summary or "").split(". "):
        if line.strip():
            print(f"  {c(DIM, '  ' + line.strip() + '.')}")

    print()
    print(c(DIM, f"  Full replay: {base}/incidents/{incident_id}/replay/ui"))
    print()

    return incident_id, incident


def run_regression_scenario(base: str, auto_approve: bool) -> None:
    """Fire first incident, then fire a second identical alert after a brief pause.

    The second alert fires for the same service — RunbookAI detects the pattern,
    warns the agent in its system prompt not to just restart again, and the agent
    escalates with a deeper root-cause analysis instead.
    """
    print(c(DIM, "  This scenario fires the same alert TWICE for the same service."))
    print(c(DIM, "  Watch how RunbookAI detects the regression and changes its response."))
    print()

    # First incident — normal checkout latency
    id_a, incident_a = run_single_scenario(
        base, "checkout", auto_approve,
        label_prefix="INCIDENT #1 — checkout-latency (first occurrence)",
    )

    # Give the DB a moment to commit the first incident
    print()
    print(c(YELLOW, "  ━━━ Simulating 30-second gap (same alert fires again) ━━━"))
    print(c(DIM, "  RunbookAI restart cleared symptoms temporarily but latency is back..."))
    print()
    for i in range(5, 0, -1):
        print(f"\r  {c(DIM, f'Firing second alert in {i}s...')} ", end="", flush=True)
        time.sleep(1)
    print(f"\r  {c(RED, 'Second alert firing now!')}                    ")
    print()

    # Second incident — regression
    id_b, incident_b = run_single_scenario(
        base, "regression", auto_approve,
        label_prefix="INCIDENT #2 — REGRESSION DETECTED (same service, 5 min later)",
    )

    # Show cross-incident diff
    section("CROSS-INCIDENT DIFF  (what other tools cannot show you)")
    diff_resp = get(base, f"/incidents/compare?incident_a={id_a}&incident_b={id_b}")
    if "error" in diff_resp:
        print(c(DIM, f"  (diff not available: {diff_resp['error']})"))
    else:
        diff = diff_resp.get("diff", {})
        ia = diff_resp.get("incident_a", {})
        ib = diff_resp.get("incident_b", {})

        print(f"  {c(BOLD, 'Incident A')}  status={ia.get('status')}  "
              f"steps={ia.get('steps')}  MTTR={ia.get('mttr_seconds', 'n/a')}s")
        print(f"  {c(BOLD, 'Incident B')}  status={ib.get('status')}  "
              f"steps={ib.get('steps')}  MTTR={ib.get('mttr_seconds', 'n/a')}s  "
              + (c(RED, "← REGRESSION") if ib.get("possible_regression") else ""))
        print()

        if diff.get("is_regression"):
            print(c(RED, "  !  RunbookAI flagged this as a CONFIRMED REGRESSION"))

        gap = diff.get("gap_minutes")
        if gap is not None:
            print(c(DIM, f"  Gap between incidents: {gap} minutes"))

        outcome = diff.get("outcome_changed")
        if outcome:
            print(c(YELLOW, "  Outcome changed between incidents"))

        added = diff.get("tools_added_in_b", [])
        dropped = diff.get("tools_dropped_in_b", [])
        if added:
            print(c(CYAN, f"  Tools added in B: {', '.join(added)}"))
        if dropped:
            print(c(DIM, f"  Tools dropped in B: {', '.join(dropped)}"))

        metric_changes = diff.get("metric_changes", {})
        if metric_changes:
            print()
            print(c(BOLD, "  Metric changes:"))
            for metric, change in metric_changes.items():
                a_val = change.get("a", "n/a")
                b_val = change.get("b", "n/a")
                print(f"    {c(DIM, metric.ljust(25))}  A={a_val}  →  B={b_val}")

    print()
    analysis = get(base, "/incidents/analysis?hours=1")
    regressions = analysis.get("regressions_detected", 0)
    if regressions:
        print(c(RED, f"  >> Pattern analysis: {regressions} regression(s) in the last hour"))
    auto_rate = analysis.get("auto_resolution_rate_pct", 0)
    total = analysis.get("total_incidents", 0)
    print(c(DIM, f"  >> {total} incidents processed  |  {auto_rate}% auto-resolution rate"))
    print()


def print_header() -> None:
    print()
    print(c(BOLD, "  ██████╗ ██╗   ██╗███╗   ██╗██████╗  ██████╗  ██████╗ ██╗  ██╗ █████╗ ██╗"))
    print(c(BOLD, "  ██╔══██╗██║   ██║████╗  ██║██╔══██╗██╔═══██╗██╔═══██╗██║ ██╔╝██╔══██╗██║"))
    print(c(BOLD, "  ██████╔╝██║   ██║██╔██╗ ██║██████╔╝██║   ██║██║   ██║█████╔╝ ███████║██║"))
    print(c(BOLD, "  ██╔══██╗██║   ██║██║╚██╗██║██╔══██╗██║   ██║██║   ██║██╔═██╗ ██╔══██║██║"))
    print(c(BOLD, "  ██║  ██║╚██████╔╝██║ ╚████║██████╔╝╚██████╔╝╚██████╔╝██║  ██╗██║  ██║██║"))
    print(c(BOLD, "  ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝"))
    print()
    print(c(DIM, "  Autonomous incident response. Gets paged. Reads the runbook. Acts."))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="RunbookAI automated demo")
    parser.add_argument(
        "scenario",
        nargs="?",
        default="checkout",
        choices=list(ALERTS.keys()),
        help="Demo scenario to run",
    )
    parser.add_argument("--no-approve", action="store_true", help="Don't auto-approve actions")
    parser.add_argument("--url", default="http://localhost:7000", help="RunbookAI base URL")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    scenario = args.scenario
    auto_approve = not args.no_approve

    # Check the server is up
    try:
        get(base, "/health")
    except Exception:
        print(c(RED, f"\n  ✗  Cannot reach RunbookAI at {base}"))
        print(c(DIM,  "     Start it with: DEMO_MODE=true uvicorn runbookai.main:app --port 7000"))
        sys.exit(1)

    print_header()

    if scenario == "regression":
        run_regression_scenario(base, auto_approve)
        print_competitor_table()
        hr()
        print()
        return

    incident_id, incident = run_single_scenario(base, scenario, auto_approve)
    status = incident.get("status", "unknown")

    if status == "resolved":
        print(c(DIM, f"  Full replay: {base}/incidents/{incident_id}/replay/ui"))
    print()
    print_competitor_table()
    hr()
    print()


if __name__ == "__main__":
    main()
def main():
    print('Test message from the demo script')

if __name__ == '__main__':
    main()