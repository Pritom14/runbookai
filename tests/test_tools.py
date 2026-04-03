"""Tests for the RunbookAI agent tool implementations.

All SSH tools are tested by mocking ssh_execute so no real SSH connection is needed.
Tests cover:
  - check_disk: parsing, critical mount detection, error passthrough
  - check_processes: running / not-running cases
  - query_metrics: CPU/mem/load extraction, graceful handling of unexpected output
  - run_db_check: postgres output passthrough, unsupported db_type error
  - clear_disk: path safety guardrail, happy path, SSH error passthrough
  - credentials: DB-first lookup, settings fallback, missing creds error
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runbookai.agent.tools import (
    check_disk,
    check_processes,
    clear_disk,
    query_metrics,
    run_db_check,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(stdout: str, stderr: str = "", exit_code: int = 0) -> dict:
    return {"status": "ok", "stdout": stdout, "stderr": stderr, "exit_code": exit_code}


def _err(msg: str) -> dict:
    return {"status": "error", "error": msg}


# ---------------------------------------------------------------------------
# check_disk
# ---------------------------------------------------------------------------

DF_OUTPUT = """\
Filesystem     Use% Used Avail Size
/              45%  10G  12G   22G
/var/log       92%  18G   2G   20G
/tmp           10%   1G   9G   10G
"""


@pytest.mark.asyncio
async def test_check_disk_parses_mounts():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(DF_OUTPUT)
        result = await check_disk("host1")

    assert result["status"] == "ok"
    assert len(result["mounts"]) == 3
    assert result["mounts"][0]["mount"] == "/"
    assert result["mounts"][0]["used_pct"] == "45%"


@pytest.mark.asyncio
async def test_check_disk_flags_critical_mounts():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(DF_OUTPUT)
        result = await check_disk("host1")

    # /var/log at 92% should be critical, / at 45% and /tmp at 10% should not.
    critical_mounts = [m["mount"] for m in result["critical_mounts"]]
    assert "/var/log" in critical_mounts
    assert "/" not in critical_mounts
    assert "/tmp" not in critical_mounts


@pytest.mark.asyncio
async def test_check_disk_passes_through_ssh_error():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _err("Connection refused")
        result = await check_disk("host1")

    assert result["status"] == "error"
    assert "Connection refused" in result["error"]


@pytest.mark.asyncio
async def test_check_disk_empty_output():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok("Filesystem     Use% Used Avail Size\n")
        result = await check_disk("host1")

    assert result["status"] == "ok"
    assert result["mounts"] == []
    assert result["critical_mounts"] == []


# ---------------------------------------------------------------------------
# check_processes
# ---------------------------------------------------------------------------

PS_OUTPUT_RUNNING = """\
1234 0.5 1.2 /usr/bin/payment-service --port 8080
1235 0.1 0.8 /usr/bin/payment-service --port 8081
"""

PS_OUTPUT_EMPTY = ""


@pytest.mark.asyncio
async def test_check_processes_running():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(PS_OUTPUT_RUNNING)
        result = await check_processes("host1", "payment-service")

    assert result["status"] == "ok"
    assert result["running"] is True
    assert result["count"] == 2
    assert result["processes"][0]["pid"] == "1234"
    assert result["processes"][0]["cpu_pct"] == "0.5"


@pytest.mark.asyncio
async def test_check_processes_not_running():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(PS_OUTPUT_EMPTY)
        result = await check_processes("host1", "payment-service")

    assert result["status"] == "ok"
    assert result["running"] is False
    assert result["count"] == 0
    assert result["processes"] == []


@pytest.mark.asyncio
async def test_check_processes_ssh_error():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _err("timeout")
        result = await check_processes("host1", "nginx")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# query_metrics
# ---------------------------------------------------------------------------

TOP_OUTPUT = """\
---CPU---
%Cpu(s):  5.2 us,  1.0 sy,  0.0 ni, 92.3 id,  0.0 wa,  0.0 hi,  1.5 si,  0.0 st
---MEM---
              total        used        free
Mem:          16000        6000        8000
Swap:          2048           0        2048
---LOAD---
 12:00:00 up 10 days,  load average: 0.82, 1.05, 1.12
"""


@pytest.mark.asyncio
async def test_query_metrics_parses_cpu():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(TOP_OUTPUT)
        result = await query_metrics("host1")

    assert result["status"] == "ok"
    assert result["cpu_used_pct"] == 7.7  # 100 - 92.3


@pytest.mark.asyncio
async def test_query_metrics_parses_memory():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(TOP_OUTPUT)
        result = await query_metrics("host1")

    assert result["memory_mb"]["total_mb"] == 16000
    assert result["memory_mb"]["used_mb"] == 6000
    assert result["memory_mb"]["free_mb"] == 8000


@pytest.mark.asyncio
async def test_query_metrics_parses_load():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(TOP_OUTPUT)
        result = await query_metrics("host1")

    assert result["load_average"]["1m"] == "0.82"
    assert result["load_average"]["5m"] == "1.05"


@pytest.mark.asyncio
async def test_query_metrics_graceful_on_unexpected_output():
    # Should not crash if top/free output format is different.
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok("unexpected output format")
        result = await query_metrics("host1")

    assert result["status"] == "ok"
    assert result["cpu_used_pct"] is None
    assert result["load_average"] == {}
    assert result["memory_mb"] == {}


@pytest.mark.asyncio
async def test_query_metrics_ssh_error():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _err("host unreachable")
        result = await query_metrics("host1")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# run_db_check
# ---------------------------------------------------------------------------

PSQL_OUTPUT = """\
 total_connections | active | idle_in_tx | longest_query_s
-------------------+--------+------------+-----------------
                98 |     87 |         12 |              45
(1 row)

 lock_count
------------
          3
(1 row)
"""


@pytest.mark.asyncio
async def test_run_db_check_postgres_ok():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _ok(PSQL_OUTPUT)
        result = await run_db_check("host1", "postgres")

    assert result["status"] == "ok"
    assert result["db_type"] == "postgres"
    # Raw output contains the key diagnostic numbers.
    assert "98" in result["raw"]
    assert "idle_in_tx" in result["raw"]


@pytest.mark.asyncio
async def test_run_db_check_unsupported_db_type():
    result = await run_db_check("host1", "mysql")

    assert result["status"] == "error"
    assert "Unsupported" in result["error"]
    assert "postgres" in result["error"]


@pytest.mark.asyncio
async def test_run_db_check_ssh_error():
    with patch("runbookai.agent.tools.ssh_execute", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = _err("psql: command not found")
        result = await run_db_check("host1", "postgres")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# clear_disk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_disk_rejects_unsafe_path():
    result = await clear_disk("host1", path="/home/ubuntu")

    assert result["status"] == "error"
    assert "not in an allowed prefix" in result["error"]
    assert "/home/ubuntu" in result["error"]


@pytest.mark.asyncio
async def test_clear_disk_rejects_root_path():
    result = await clear_disk("host1", path="/")

    assert result["status"] == "error"
    assert "not in an allowed prefix" in result["error"]


@pytest.mark.asyncio
async def test_clear_disk_happy_path(monkeypatch):
    ssh_responses = [
        _ok("42\n"),       # dry-run: wc -l count
        _ok("done\n"),     # delete command
        # check_disk calls ssh_execute again for df
        _ok(DF_OUTPUT),
    ]
    call_count = 0

    async def mock_ssh(host, command, **kwargs):
        nonlocal call_count
        resp = ssh_responses[min(call_count, len(ssh_responses) - 1)]
        call_count += 1
        return resp

    monkeypatch.setattr("runbookai.agent.tools.ssh_execute", mock_ssh)
    result = await clear_disk("host1", path="/var/log", older_than_days=7)

    assert result["status"] == "ok"
    assert result["files_deleted"] == "42"
    assert result["path"] == "/var/log"


@pytest.mark.asyncio
async def test_clear_disk_allows_tmp_path(monkeypatch):
    ssh_responses = [_ok("0\n"), _ok("done\n"), _ok(DF_OUTPUT)]
    call_count = 0

    async def mock_ssh(host, command, **kwargs):
        nonlocal call_count
        resp = ssh_responses[min(call_count, len(ssh_responses) - 1)]
        call_count += 1
        return resp

    monkeypatch.setattr("runbookai.agent.tools.ssh_execute", mock_ssh)
    result = await clear_disk("host1", path="/tmp", older_than_days=1)

    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_clear_disk_passes_through_delete_error(monkeypatch):
    ssh_responses = [
        _ok("10\n"),           # dry-run count
        _err("Permission denied"),  # delete fails
    ]
    call_count = 0

    async def mock_ssh(host, command, **kwargs):
        nonlocal call_count
        resp = ssh_responses[min(call_count, len(ssh_responses) - 1)]
        call_count += 1
        return resp

    monkeypatch.setattr("runbookai.agent.tools.ssh_execute", mock_ssh)
    result = await clear_disk("host1", path="/var/log")

    assert result["status"] == "error"
    assert "Permission denied" in result["error"]


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ssh_creds_from_db():
    from runbookai.agent.credentials import get_ssh_creds
    from runbookai.models import HostCredential

    mock_row = HostCredential(
        hostname="prod-1",
        username="deploy",
        private_key_pem="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        port=2222,
    )
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_row
    mock_session.execute = AsyncMock(return_value=mock_result)

    creds = await get_ssh_creds("prod-1", mock_session)

    assert creds.username == "deploy"
    assert creds.port == 2222
    assert creds.private_key_pem is not None


@pytest.mark.asyncio
async def test_get_ssh_creds_fallback_to_settings():
    from runbookai.agent.credentials import get_ssh_creds

    # No DB row — falls back to settings.
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("runbookai.agent.credentials.settings") as mock_settings:
        mock_settings.ssh_default_username = "ubuntu"
        mock_settings.ssh_private_key_path = "/home/ubuntu/.ssh/id_rsa"
        creds = await get_ssh_creds("unknown-host", mock_session)

    assert creds.username == "ubuntu"
    assert creds.private_key_path == "/home/ubuntu/.ssh/id_rsa"


@pytest.mark.asyncio
async def test_get_ssh_creds_raises_when_not_configured():
    from runbookai.agent.credentials import SSHConfigurationError, get_ssh_creds

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("runbookai.agent.credentials.settings") as mock_settings:
        mock_settings.ssh_default_username = ""  # not configured
        with pytest.raises(SSHConfigurationError):
            await get_ssh_creds("unknown-host", mock_session)
