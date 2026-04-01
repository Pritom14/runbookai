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


async def ssh_execute(host: str, command: str, timeout_s: int = 30) -> dict[str, Any]:
    """Execute a shell command on a remote host via SSH."""
    logger.info("ssh_execute: host=%s command=%s", host, command)
    try:
        import asyncssh

        async with asyncssh.connect(host, known_hosts=None, timeout=timeout_s) as conn:
            result = await conn.run(command, timeout=timeout_s)
        return {
            "status": "ok",
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "exit_code": result.exit_status,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_logs(host: str, service: str, lines: int = 100) -> dict[str, Any]:
    """Tail the last N lines of a service log on a remote host via journalctl."""
    logger.info("check_logs: host=%s service=%s lines=%d", host, service, lines)
    return await ssh_execute(host, f"journalctl -u {service} -n {lines} --no-pager")


async def restart_service(host: str, service: str) -> dict[str, Any]:
    """Restart a systemd service on a remote host.

    TODO: SSH in, run `systemctl restart {service}`, verify status.
    HIGH-RISK — always requires approval in Suggest Mode.
    """
    logger.info("restart_service: host=%s service=%s", host, service)
    return {"status": "not_implemented", "host": host, "service": service}


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
    "http_check": http_check,
    "scale_service": scale_service,
    "finish": finish,
}

# Tools that always require human approval even in autonomous mode.
HIGH_RISK_TOOLS: frozenset[str] = frozenset(
    {"restart_service", "scale_service", "ssh_execute"}
)

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
