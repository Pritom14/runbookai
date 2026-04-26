"""SSH credential resolution for the RunbookAI agent.

Lookup order for a given hostname:
  1. `host_credentials` DB row (per-host override).
  2. Global defaults: SSH_DEFAULT_USERNAME + SSH_PRIVATE_KEY_PATH env vars.
  3. Raise ConfigurationError if neither is available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from runbookai.config import settings

logger = logging.getLogger("runbookai.credentials")


class SSHConfigurationError(Exception):
    """Raised when no SSH credentials are available for a host."""


@dataclass
class SSHCredentials:
    username: str
    port: int = 22
    # Exactly one of private_key_pem or private_key_path will be set.
    private_key_pem: Optional[str] = None
    private_key_path: Optional[str] = None


async def get_ssh_creds(host: str, session: Optional[object]) -> SSHCredentials:
    """Return SSH credentials for *host*.

    Parameters
    ----------
    host:
        Hostname or IP (as passed to the ssh_execute tool).
    session:
        An active SQLAlchemy async session.  May be None in tests.
    """
    if session is not None:
        from runbookai.models import HostCredential

        result = await session.execute(
            select(HostCredential).where(HostCredential.hostname == host)
        )
        row: Optional[HostCredential] = result.scalar_one_or_none()
        if row is not None:
            logger.debug("ssh creds: DB row found for host=%s user=%s", host, row.username)
            return SSHCredentials(
                username=row.username,
                port=row.port,
                private_key_pem=row.private_key_pem or None,
            )

    # Fall back to global settings.
    username = settings.ssh_default_username
    key_path = settings.ssh_private_key_path
    if not username:
        raise SSHConfigurationError(
            f"No SSH credentials configured for host '{host}'. "
            "Register via POST /api/hosts or set SSH_DEFAULT_USERNAME in .env."
        )
    logger.debug("ssh creds: using global defaults for host=%s user=%s", host, username)
    return SSHCredentials(
        username=username,
        private_key_path=key_path or None,
    )
