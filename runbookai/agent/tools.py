"""Tool registry for the RunbookAI agent.

Each tool is a plain async callable. The registry holds both the callable and
the Anthropic tool-use schema (name + description + input_schema) so the same
dict drives both LLM tool-choice and local dispatch — matching claw-code's
pattern of keeping schema and implementation co-located.

All tools return a dict with at minimum {"status": "ok" | "error", ...}.
High-risk tools are flagged and always require human approval in Suggest Mode.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("runbookai.tools")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def ssh_execute(
    host: str, command: str, timeout_s: int = 30, _session: Any = None
) -> dict[str, Any]:
    """Execute a shell command on a remote host via SSH."""
    logger.info("ssh_execute: host=%s command=%s", host, command)
    try:
        import asyncssh

        from runbookai.agent.credentials import SSHConfigurationError, get_ssh_creds

        try:
            creds = await get_ssh_creds(host, _session)
        except SSHConfigurationError as e:
            return {"status": "error", "error": str(e)}

        connect_kwargs: dict[str, Any] = {
            "host": host,
            "username": creds.username,
            "port": creds.port,
            "known_hosts": None,
            "connect_timeout": timeout_s,
        }
        if creds.private_key_pem:
            connect_kwargs["client_keys"] = [asyncssh.import_private_key(creds.private_key_pem)]
        elif creds.private_key_path:
            connect_kwargs["client_keys"] = [creds.private_key_path]

        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await conn.run(command, timeout=timeout_s)
        return {
            "status": "ok",
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "exit_code": result.exit_status,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_logs(
    host: str, service: str, lines: int = 100, _session: Any = None
) -> dict[str, Any]:
    """Tail the last N lines of a service log on a remote host via journalctl."""
    logger.info("check_logs: host=%s service=%s lines=%d", host, service, lines)
    return await ssh_execute(
        host, f"journalctl -u {service} -n {lines} --no-pager", _session=_session
    )


async def restart_service(
    host: str, service: str, _session: Any = None
) -> dict[str, Any]:
    """Restart a systemd service on a remote host via SSH.

    HIGH-RISK — always requires approval in Suggest Mode.
    """
    logger.info("restart_service: host=%s service=%s", host, service)
    # Restart then immediately check status so the agent knows if it worked.
    restart = await ssh_execute(
        host, f"systemctl restart {service}", _session=_session
    )
    if restart["status"] == "error":
        return restart
    status = await ssh_execute(
        host, f"systemctl is-active {service}", _session=_session
    )
    active = status.get("stdout", "").strip() == "active"
    return {
        "status": "ok",
        "service": service,
        "active": active,
        "restart_exit_code": restart.get("exit_code"),
        "is_active_stdout": status.get("stdout", "").strip(),
    }


async def check_disk(host: str, _session: Any = None) -> dict[str, Any]:
    """Check disk usage on a remote host (df -h). Returns per-mount usage."""
    logger.info("check_disk: host=%s", host)
    result = await ssh_execute(
        host, "df -h --output=target,pcent,used,avail,size", _session=_session
    )
    if result["status"] == "error":
        return result
    # Parse into structured rows for easier LLM consumption.
    lines = result["stdout"].strip().splitlines()
    mounts = []
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 5:
            mounts.append({
                "mount": parts[0],
                "used_pct": parts[1],
                "used": parts[2],
                "avail": parts[3],
                "size": parts[4],
            })
    critical = [
        m for m in mounts
        if m["used_pct"].rstrip("%").isdigit() and int(m["used_pct"].rstrip("%")) >= 80
    ]
    return {
        "status": "ok",
        "mounts": mounts,
        "critical_mounts": critical,
        "raw": result["stdout"],
    }


async def check_processes(host: str, process_name: str, _session: Any = None) -> dict[str, Any]:
    """Check if a process is running on a remote host. Returns PID, CPU%, memory%."""
    logger.info("check_processes: host=%s process=%s", host, process_name)
    # Use pgrep -a for a clean list, fall back to ps for details.
    result = await ssh_execute(
        host,
        f"ps aux | grep '[{process_name[0]}]{process_name[1:]}' | awk '{{print $2,$3,$4,$11}}'",
        _session=_session,
    )
    if result["status"] == "error":
        return result
    lines = [ln for ln in result["stdout"].strip().splitlines() if ln]
    processes = []
    for line in lines:
        parts = line.split(maxsplit=3)
        if len(parts) >= 4:
            processes.append({
                "pid": parts[0],
                "cpu_pct": parts[1],
                "mem_pct": parts[2],
                "command": parts[3],
            })
    return {
        "status": "ok",
        "process_name": process_name,
        "running": len(processes) > 0,
        "count": len(processes),
        "processes": processes,
    }


async def query_metrics(host: str, _session: Any = None) -> dict[str, Any]:
    """Fetch CPU, memory, and load average from a remote host."""
    logger.info("query_metrics: host=%s", host)
    cmd = (
        "echo '---CPU---' && top -bn1 | grep 'Cpu(s)' && "
        "echo '---MEM---' && free -m && "
        "echo '---LOAD---' && uptime"
    )
    result = await ssh_execute(host, cmd, _session=_session)
    if result["status"] == "error":
        return result
    raw = result["stdout"]
    # Extract key numbers for structured output.
    import re

    cpu_idle_m = re.search(r"(\d+\.\d+)\s+id", raw)
    cpu_used = round(100 - float(cpu_idle_m.group(1)), 1) if cpu_idle_m else None
    load_m = re.search(r"load average:\s+([\d.]+),\s+([\d.]+),\s+([\d.]+)", raw)
    load = {"1m": load_m.group(1), "5m": load_m.group(2), "15m": load_m.group(3)} if load_m else {}
    mem_m = re.search(r"Mem:\s+(\d+)\s+(\d+)\s+(\d+)", raw)
    memory = (
        {
            "total_mb": int(mem_m.group(1)),
            "used_mb": int(mem_m.group(2)),
            "free_mb": int(mem_m.group(3)),
        }
        if mem_m
        else {}
    )
    return {
        "status": "ok",
        "cpu_used_pct": cpu_used,
        "load_average": load,
        "memory_mb": memory,
        "raw": raw,
    }


async def run_db_check(
    host: str, db_type: str = "postgres", _session: Any = None
) -> dict[str, Any]:
    """Check database health: active connections, long-running queries, locks.

    Supported db_type values: "postgres" (default).
    Requires psql to be installed on the host and local peer auth (or env vars).
    """
    logger.info("run_db_check: host=%s db_type=%s", host, db_type)
    if db_type == "postgres":
        cmd = (
            "psql -U postgres -c \""
            "SELECT count(*) AS total_connections, "
            "sum(CASE WHEN state='active' THEN 1 ELSE 0 END) AS active, "
            "sum(CASE WHEN state='idle in transaction' THEN 1 ELSE 0 END) AS idle_in_tx, "
            "max(EXTRACT(EPOCH FROM now()-query_start))::int AS longest_query_s "
            "FROM pg_stat_activity WHERE datname IS NOT NULL;\" 2>&1 && "
            "psql -U postgres -c \""
            "SELECT count(*) AS lock_count FROM pg_locks WHERE NOT granted;\" 2>&1"
        )
    else:
        return {"status": "error", "error": f"Unsupported db_type: {db_type}. Use 'postgres'."}
    result = await ssh_execute(host, cmd, _session=_session)
    if result["status"] == "error":
        return result
    return {
        "status": "ok",
        "db_type": db_type,
        "raw": result["stdout"],
    }


async def clear_disk(
    host: str, path: str = "/var/log", older_than_days: int = 7, _session: Any = None
) -> dict[str, Any]:
    """Delete files older than N days under a path to free disk space.

    HIGH-RISK — permanently deletes files. Always requires approval in Suggest Mode.
    Only operates under /var/log or /tmp to prevent accidental data loss.
    """
    logger.info("clear_disk: host=%s path=%s days=%d", host, path, older_than_days)
    # Safety guardrail: only allow safe paths.
    allowed_prefixes = ("/var/log", "/tmp")
    if not any(path.startswith(p) for p in allowed_prefixes):
        return {
            "status": "error",
            "error": (
                f"Path '{path}' is not in an allowed prefix {allowed_prefixes}. Refusing to delete."
            ),
        }
    # Dry-run first to count what would be deleted.
    dry = await ssh_execute(
        host,
        f"find {path} -type f -mtime +{older_than_days} | wc -l",
        _session=_session,
    )
    file_count = dry.get("stdout", "0").strip()
    # Actual delete.
    delete = await ssh_execute(
        host,
        f"find {path} -type f -mtime +{older_than_days} -delete && echo 'done'",
        _session=_session,
    )
    if delete["status"] == "error":
        return delete
    # Check disk after.
    after = await check_disk(host, _session=_session)
    return {
        "status": "ok",
        "path": path,
        "older_than_days": older_than_days,
        "files_deleted": file_count,
        "disk_after": after.get("mounts", []),
    }


async def http_check(url: str, expected_status: int = 200) -> dict[str, Any]:
    """Perform an HTTP health check against a URL."""
    logger.info("http_check: url=%s expected_status=%d", url, expected_status)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            start = time.monotonic()
            response = await client.get(url)
            latency_ms = int((time.monotonic() - start) * 1000)
        healthy = response.status_code == expected_status
        return {
            "status": "ok",
            "healthy": healthy,
            "status_code": response.status_code,
            "expected_status": expected_status,
            "latency_ms": latency_ms,
            "body_snippet": response.text[:200],
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "healthy": False}


async def scale_service(service: str, replicas: int) -> dict[str, Any]:
    """Scale a Kubernetes deployment to N replicas.

    TODO: Use kubectl or the k8s Python client.
    HIGH-RISK — always requires approval in Suggest Mode.
    """
    logger.info("scale_service: service=%s replicas=%d", service, replicas)
    return {"status": "not_implemented", "service": service, "replicas": replicas}


async def finish(resolution_summary: str) -> dict[str, Any]:
    """Signal that the incident is resolved. Always the last tool called.

    The agent calls this when it believes the incident is remediated or when
    it determines that escalation is required (set resolved=false and explain).
    """
    logger.info("finish: summary=%s", resolution_summary[:120])
    return {"status": "ok", "resolution_summary": resolution_summary}


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic tool-use format)
# Each entry: {"name", "description", "input_schema"} — passed verbatim in
# the `tools=` list on every client.messages.create() call.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "ssh_execute",
        "description": (
            "Execute an arbitrary shell command on a remote host via SSH. "
            "Use for one-off diagnostic commands. HIGH-RISK."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname or IP"},
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout_s": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": 30,
                },
            },
            "required": ["host", "command"],
        },
    },
    {
        "name": "check_logs",
        "description": "Tail the last N lines of a service log on a remote host.",
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "service": {"type": "string", "description": "systemd service name"},
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to return",
                    "default": 100,
                },
            },
            "required": ["host", "service"],
        },
    },
    {
        "name": "restart_service",
        "description": (
            "Restart a systemd service on a remote host. HIGH-RISK — "
            "causes a brief service interruption."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "service": {"type": "string"},
            },
            "required": ["host", "service"],
        },
    },
    {
        "name": "check_disk",
        "description": (
            "Check disk usage on a remote host (df -h). Returns per-mount utilisation. "
            "Flags mounts at ≥80% as critical. Use this when investigating disk-full or "
            "storage-related alerts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hostname or IP"},
            },
            "required": ["host"],
        },
    },
    {
        "name": "check_processes",
        "description": (
            "Check whether a named process is running on a remote host. "
            "Returns PID, CPU%, and memory% for each matching process. "
            "Use this to verify a service is alive before checking logs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "process_name": {
                    "type": "string",
                    "description": "Process name substring to search for (e.g. 'nginx', 'python')",
                },
            },
            "required": ["host", "process_name"],
        },
    },
    {
        "name": "query_metrics",
        "description": (
            "Fetch real-time CPU usage, memory, and load average from a remote host. "
            "Use this when diagnosing latency spikes, OOM kills, or performance degradation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
            },
            "required": ["host"],
        },
    },
    {
        "name": "run_db_check",
        "description": (
            "Query database health on a remote host: active connections, idle-in-transaction "
            "count, longest running query, and ungranted locks. "
            "Use this when services report connection timeouts or slow queries. "
            "Supported db_type: 'postgres'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "db_type": {
                    "type": "string",
                    "description": "Database type: 'postgres'",
                    "default": "postgres",
                },
            },
            "required": ["host"],
        },
    },
    {
        "name": "clear_disk",
        "description": (
            "Delete files older than N days under a path to free disk space. HIGH-RISK. "
            "Only operates under /var/log or /tmp. Always requires human approval. "
            "Use this after check_disk confirms a mount is critically full."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Directory to clean. Must be under /var/log or /tmp.",
                    "default": "/var/log",
                },
                "older_than_days": {
                    "type": "integer",
                    "description": "Delete files older than this many days",
                    "default": 7,
                },
            },
            "required": ["host"],
        },
    },
    {
        "name": "http_check",
        "description": "Perform an HTTP health check against a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "expected_status": {
                    "type": "integer",
                    "default": 200,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "scale_service",
        "description": (
            "Scale a Kubernetes deployment to N replicas. HIGH-RISK — "
            "affects running capacity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Kubernetes deployment name"},
                "replicas": {"type": "integer"},
            },
            "required": ["service", "replicas"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Signal that the incident is resolved (or that human escalation is needed). "
            "Always call this as the final step. Include a plain-English summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resolution_summary": {
                    "type": "string",
                    "description": "What was done and current status",
                },
            },
            "required": ["resolution_summary"],
        },
    },
]

# ---------------------------------------------------------------------------
# Dispatch registry: tool name → async callable
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Any] = {
    "ssh_execute": ssh_execute,
    "check_logs": check_logs,
    "restart_service": restart_service,
    "check_disk": check_disk,
    "check_processes": check_processes,
    "query_metrics": query_metrics,
    "run_db_check": run_db_check,
    "clear_disk": clear_disk,
    "http_check": http_check,
    "scale_service": scale_service,
    "finish": finish,
}

# Tools that always require human approval even in autonomous mode.
HIGH_RISK_TOOLS: frozenset[str] = frozenset(
    {"restart_service", "scale_service", "ssh_execute", "clear_disk"}
)

# Tools that need the DB session injected as `_session` for credential lookup.
SESSION_AWARE_TOOLS: frozenset[str] = frozenset({
    "ssh_execute", "check_logs", "restart_service",
    "check_disk", "check_processes", "query_metrics", "run_db_check", "clear_disk",
})

# ---------------------------------------------------------------------------
# OpenAI-compatible tool schemas (for Ollama and OpenAI API usage)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS_OPENAI: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["input_schema"],
        },
    }
    for schema in TOOL_SCHEMAS
]
