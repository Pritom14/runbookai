"""Load runbooks from YAML files on startup."""

import logging
import pathlib
from typing import Optional

import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from runbookai.models import Runbook
from runbookai.database import AsyncSessionLocal

logger = logging.getLogger("runbookai.runbook_loader")


async def load_runbooks_from_disk() -> None:
    """Discover and load all YAML runbooks from runbookai/runbooks/ directory.

    Stores them in the database so they persist across restarts.
    Logs each loaded runbook to stdout.
    """
    runbooks_dir = pathlib.Path(__file__).parent / "runbooks"

    if not runbooks_dir.exists():
        logger.warning("Runbooks directory does not exist: %s", runbooks_dir)
        return

    yaml_files = sorted(runbooks_dir.glob("*.yaml"))
    if not yaml_files:
        logger.info("No YAML runbooks found in %s", runbooks_dir)
        return

    async with AsyncSessionLocal() as session:
        for yaml_file in yaml_files:
            try:
                await _load_single_runbook(session, yaml_file)
            except Exception as e:
                logger.error("Failed to load runbook from %s: %s", yaml_file, e, exc_info=True)


async def _load_single_runbook(session: AsyncSession, yaml_file: pathlib.Path) -> None:
    """Load a single YAML runbook file into the database.

    Updates existing runbook if name already exists, otherwise creates new.
    """
    with open(yaml_file, "r") as f:
        data = yaml.safe_load(f)

    if not data or "name" not in data:
        logger.warning("Runbook file %s missing 'name' field, skipping", yaml_file)
        return

    name = data.get("name")
    alert_pattern = data.get("alert_pattern", "")

    # Serialize the entire YAML content as the runbook content
    content = yaml_file.read_text()

    # Check if runbook with this name already exists
    existing = await session.execute(
        select(Runbook).where(Runbook.name == name)
    )
    runbook = existing.scalar_one_or_none()

    if runbook:
        # Update existing runbook
        runbook.alert_pattern = alert_pattern
        runbook.content = content
        await session.commit()
        logger.info("Updated runbook: name=%s alert_pattern=%s file=%s", name, alert_pattern, yaml_file.name)
    else:
        # Create new runbook
        runbook = Runbook(
            name=name,
            alert_pattern=alert_pattern,
            content=content,
        )
        session.add(runbook)
        await session.commit()
        logger.info("Loaded runbook: name=%s alert_pattern=%s file=%s", name, alert_pattern, yaml_file.name)
