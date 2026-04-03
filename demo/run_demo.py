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

def _request(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(), "status_code": e.code}


def get(base: str, path: str) -> dict:
    return _request("GET", f"{base}{path}")


def post(base: str, path: str, body: dict | None = None) -> dict:
    return _request("POST", f"{base}{path}", body)


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
}

SCENARIO_LABELS = {
    "checkout": "checkout-latency  —  DB connection leak → escalate with root cause",
    "payment":  "payment-service-503  —  OOM crash → restart → recover",
    "disk":     "disk-full  —  /var/log 94% → clear old logs → verify",
}

# ---------------------------------------------------------------------------
# Tool output formatters
# ---------------------------------------------------------------------------

TOOL_ICONS = {
    "http_check":        "🌐",
    "check_logs":        "📋",
    "check_disk":        "💾",
    "check_processes":   "⚙️ ",
    "query_metrics":     "📊",
    "run_db_check":      "🗄️ ",
    "restart_service":   "🔄",
    "clear_disk":        "🧹",
    "ssh_execute":       "🖥️ ",
    "scale_service":     "📈",
    "finish":            "✅",
    "_event":            "◆",
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
            lines.append(f"    {c(RED, '⚠ CRITICAL')} {m['mount']}  {m['used_pct']} used")
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
        print(f"  {c(MAGENTA, f'◆ {event_name}')}")
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
        time.sleep(1.5)
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
    print(f"  {c(YELLOW, '⚠  APPROVAL REQUIRED')}")
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

    result = post(base, f"/approvals/{approval_id}/approve")
    if "error" in result:
        print(c(RED, f"  Approval failed: {result['error']}"))
    else:
        print(c(GREEN, "  ✓ Approved — agent continuing"))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    # Header
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

    section(f"SCENARIO: {SCENARIO_LABELS[scenario]}")

    alert = ALERTS[scenario]
    print(f"  {c(RED, '🚨 ALERT RECEIVED')}")
    print(f"  {c(BOLD, alert['alert_name'])}")
    print(f"  {c(DIM, alert['details'])}")
    print()

    # Fire the alert
    print(c(DIM, "  → Sending to RunbookAI..."))
    resp = post(base, "/webhooks/generic", alert)
    incident_id = resp.get("incident_id")
    if not incident_id:
        print(c(RED, f"  ✗  Failed to create incident: {resp}"))
        sys.exit(1)

    section("AGENT WORKING")

    t_start = time.monotonic()
    incident = poll_until_done(base, incident_id, auto_approve)
    elapsed = time.monotonic() - t_start

    # Final result
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
    hr()
    print()


if __name__ == "__main__":
    main()
