"""Tool registry for the RunbookAI agent.

Each tool is a callable the agent can invoke during incident response.
All tools return a dict with at minimum {"status": "ok" | "error", ...}.

TODO: Implement each tool stub before moving to autonomous mode.
"""

import logging

logger = logging.getLogger("runbookai.tools")


async def ssh_execute(host: str, command: str, timeout_s: int = 30) -> dict:
    """Execute a shell command on a remote host via SSH.

    TODO: Implement using asyncssh or paramiko.
    Credentials should come from a secrets store (not hardcoded).
    """
    logger.info("ssh_execute called: host=%s command=%s", host, command)
    return {"status": "not_implemented", "host": host, "command": command}


async def check_logs(host: str, service: str, lines: int = 100) -> dict:
    """Tail the last N lines of a service log on a remote host.

    TODO: SSH in, run `journalctl -u {service} -n {lines}` or equivalent.
    """
    logger.info("check_logs called: host=%s service=%s", host, service)
    return {"status": "not_implemented", "host": host, "service": service}


async def restart_service(host: str, service: str) -> dict:
    """Restart a systemd service on a remote host.

    TODO: SSH in, run `systemctl restart {service}`, verify status.
    High-risk action — always requires approval in Suggest Mode.
    """
    logger.info("restart_service called: host=%s service=%s", host, service)
    return {"status": "not_implemented", "host": host, "service": service}


async def http_check(url: str, expected_status: int = 200) -> dict:
    """Perform an HTTP health check against a URL.

    TODO: Use httpx, return status_code, latency_ms, body snippet.
    """
    logger.info("http_check called: url=%s", url)
    return {"status": "not_implemented", "url": url}


async def scale_service(service: str, replicas: int) -> dict:
    """Scale a Kubernetes deployment to N replicas.

    TODO: Use kubectl or k8s Python client.
    High-risk action — always requires approval in Suggest Mode.
    """
    logger.info("scale_service called: service=%s replicas=%d", service, replicas)
    return {"status": "not_implemented", "service": service, "replicas": replicas}


# Registry maps tool name → callable
TOOL_REGISTRY: dict[str, callable] = {
    "ssh_execute": ssh_execute,
    "check_logs": check_logs,
    "restart_service": restart_service,
    "http_check": http_check,
    "scale_service": scale_service,
}

# Tools that always require human approval even in autonomous mode
HIGH_RISK_TOOLS = {"restart_service", "scale_service", "ssh_execute"}
