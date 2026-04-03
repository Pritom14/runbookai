"""Demo mode — pre-canned tool responses for evaluation without real infrastructure.

When DEMO_MODE=true, all SSH-based tools return realistic fake data so anyone
can try RunbookAI without setting up SSH credentials or a real server.

The responses tell a coherent story:
  - checkout-service is slow (p99 > 4s)
  - http_check returns 200 but with 2.8s latency
  - DB has 98/100 connections active, 12 idle-in-transaction (connection leak)
  - Disk is healthy (78% on /var/log — not yet critical)
  - System resources are fine (CPU 23%, memory normal)
  - Root cause: connection leak in checkout-service → agent should escalate, not restart

This mirrors the "checkout-latency" demo scenario and showcases all diagnostic tools.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Canned responses keyed by tool name
# ---------------------------------------------------------------------------

_DEMO_RESPONSES: dict[str, dict[str, Any]] = {
    "ssh_execute": {
        "status": "ok",
        "stdout": "[demo] command executed successfully",
        "stderr": "",
        "exit_code": 0,
    },
    "check_logs": {
        "status": "ok",
        "stdout": (
            "Apr 03 11:42:01 checkout-service[2341]: INFO  Starting request\n"
            "Apr 03 11:42:02 checkout-service[2341]: WARN  Pool: 96/100 in use\n"
            "Apr 03 11:42:04 checkout-service[2341]: ERROR Timeout acquiring "
            "connection from pool (waited 3002ms) -- pool exhausted\n"
            "Apr 03 11:42:04 checkout-service[2341]: ERROR Request failed: "
            "connection pool exhausted\n"
            "Apr 03 11:42:07 checkout-service[2341]: WARN  Pool: 98/100 in use\n"
            "Apr 03 11:42:09 checkout-service[2341]: ERROR Timeout acquiring "
            "connection from pool (waited 3001ms)\n"
            "Apr 03 11:42:11 checkout-service[2341]: WARN  Slow query: "
            "SELECT * FROM orders WHERE customer_id=? took 4821ms\n"
        ),
        "stderr": "",
        "exit_code": 0,
    },
    "check_disk": {
        "status": "ok",
        "mounts": [
            {"mount": "/", "used_pct": "45%", "used": "18G", "avail": "22G", "size": "40G"},
            {"mount": "/var/log", "used_pct": "78%", "used": "15G", "avail": "4G", "size": "20G"},
            {"mount": "/tmp", "used_pct": "12%", "used": "1.2G", "avail": "8.8G", "size": "10G"},
        ],
        "critical_mounts": [],
        "raw": (
            "Filesystem     Use% Used Avail Size\n"
            "/               45%  18G   22G   40G\n"
            "/var/log        78%  15G    4G   20G\n"
            "/tmp            12% 1.2G  8.8G   10G\n"
        ),
    },
    "check_processes": {
        "status": "ok",
        "process_name": "checkout-service",
        "running": True,
        "count": 1,
        "processes": [
            {
                "pid": "2341",
                "cpu_pct": "44.7",
                "mem_pct": "8.2",
                "command": "/usr/bin/java -jar /opt/checkout-service/checkout-service.jar",
            }
        ],
    },
    "query_metrics": {
        "status": "ok",
        "cpu_used_pct": 23.4,
        "load_average": {"1m": "1.42", "5m": "1.38", "15m": "1.21"},
        "memory_mb": {"total_mb": 16384, "used_mb": 8432, "free_mb": 7952},
        "raw": (
            "---CPU---\n"
            "%Cpu(s): 23.4 us,  2.1 sy,  0.0 ni, 73.2 id\n"
            "---MEM---\n"
            "              total        used        free\n"
            "Mem:          16384        8432        7952\n"
            "---LOAD---\n"
            "load average: 1.42, 1.38, 1.21\n"
        ),
    },
    "run_db_check": {
        "status": "ok",
        "db_type": "postgres",
        "raw": (
            " total_connections | active | idle_in_tx | longest_query_s \n"
            "-------------------+--------+------------+-----------------\n"
            "                98 |     87 |         12 |              67 \n"
            "(1 row)\n\n"
            " lock_count \n"
            "------------\n"
            "          3 \n"
            "(1 row)\n"
        ),
    },
    "restart_service": {
        "status": "ok",
        "service": "checkout-service",
        "active": True,
        "restart_exit_code": 0,
        "is_active_stdout": "active",
    },
    "clear_disk": {
        "status": "ok",
        "path": "/var/log",
        "older_than_days": 7,
        "files_deleted": "156",
        "disk_after": [
            {"mount": "/", "used_pct": "45%", "used": "18G", "avail": "22G", "size": "40G"},
            {"mount": "/var/log", "used_pct": "51%", "used": "10G", "avail": "9G", "size": "20G"},
            {"mount": "/tmp", "used_pct": "12%", "used": "1.2G", "avail": "8.8G", "size": "10G"},
        ],
    },
}

# http_check has two states in the demo: first call slow (problem), second call fast (after fix)
_HTTP_CALL_COUNT: dict[str, int] = {}

_HTTP_SLOW = {
    "status": "ok",
    "healthy": True,
    "status_code": 200,
    "expected_status": 200,
    "latency_ms": 2847,
    "body_snippet": '{"status":"ok"}',
}

_HTTP_HEALTHY = {
    "status": "ok",
    "healthy": True,
    "status_code": 200,
    "expected_status": 200,
    "latency_ms": 94,
    "body_snippet": '{"status":"ok"}',
}


def demo_http_check(url: str, expected_status: int = 200) -> dict[str, Any]:
    """Return slow on the first call (problem visible), fast on subsequent calls."""
    count = _HTTP_CALL_COUNT.get(url, 0)
    _HTTP_CALL_COUNT[url] = count + 1
    return _HTTP_SLOW if count == 0 else _HTTP_HEALTHY


def get_demo_response(tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return the canned demo response for a given tool, with kwargs patched in."""
    if tool_name == "http_check":
        return demo_http_check(kwargs.get("url", ""), kwargs.get("expected_status", 200))

    base = dict(_DEMO_RESPONSES.get(tool_name, {"status": "ok", "message": "demo ok"}))

    # Patch process_name so the response matches what the LLM asked for.
    if tool_name == "check_processes" and "process_name" in kwargs:
        base = dict(base)
        base["process_name"] = kwargs["process_name"]
        if base["processes"]:
            proc = dict(base["processes"][0])
            proc["command"] = proc["command"].replace("checkout-service", kwargs["process_name"])
            base["processes"] = [proc]

    if tool_name in ("restart_service",) and "service" in kwargs:
        base = dict(base)
        base["service"] = kwargs["service"]

    return base
