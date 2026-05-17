"""Cloud incident routing and agent management."""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.models import Agent, Customer, PendingIncident

logger = logging.getLogger("runbookai.cloud.routing")


async def route_incident_to_customer(
    customer_id: Optional[str],
    incident_id: str,
    incident_payload: dict,
    session: AsyncSession,
) -> dict:
    """Route an incident to the appropriate customer's agent.

    If customer_id is None, incident is processed as a non-cloud incident.
    If customer has an online agent, returns {"status": "streamed"}.
    If customer has no online agent, queues incident and returns {"status": "queued"}.
    """
    if not customer_id:
        return {"status": "non_cloud"}

    # Check if customer exists
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()
    if not customer:
        logger.warning(
            "Incident routed to non-existent customer: customer_id=%s incident_id=%s",
            customer_id,
            incident_id,
        )
        return {"status": "customer_not_found"}

    # Check if customer has an online agent
    agent_result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id, Agent.status == "online")
        .order_by(Agent.last_heartbeat.desc())
        .limit(1)
    )
    agent = agent_result.scalars().first()

    if agent:
        logger.info(
            "Incident streamed to online agent: incident_id=%s customer_id=%s agent_id=%s",
            incident_id,
            customer_id,
            agent.id,
        )
        return {"status": "streamed", "agent_id": agent.id}
    else:
        # Queue incident for offline agent
        pending = PendingIncident(
            id=str(__import__("uuid").uuid4()),
            customer_id=customer_id,
            incident_id=incident_id,
            incident_payload=incident_payload,
        )
        session.add(pending)
        await session.flush()
        logger.info(
            "Incident queued for offline agent: incident_id=%s customer_id=%s",
            incident_id,
            customer_id,
        )
        return {"status": "queued", "pending_id": pending.id}


async def get_customer_agent_status(
    customer_id: str,
    session: AsyncSession,
) -> dict:
    """Get the status of the customer's primary agent.

    Returns agent status (online/offline/error), last heartbeat timestamp,
    and error message if applicable.
    """
    result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id)
        .order_by(Agent.last_heartbeat.desc())
        .limit(1)
    )
    agent = result.scalars().first()

    if not agent:
        return {
            "status": "no_agent",
            "message": "No agent registered for this customer",
        }

    return {
        "status": agent.status,
        "agent_id": agent.id,
        "agent_name": agent.name,
        "last_heartbeat": agent.last_heartbeat.isoformat() if agent.last_heartbeat else None,
        "error_message": agent.error_message,
    }


async def mark_agent_online(
    agent_id: str,
    session: AsyncSession,
) -> bool:
    """Mark an agent as online and update last heartbeat."""
    from datetime import datetime

    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalars().first()

    if not agent:
        return False

    agent.status = "online"
    agent.last_heartbeat = datetime.utcnow()
    agent.error_message = None
    await session.flush()
    logger.info("Agent marked online: agent_id=%s", agent_id)
    return True


async def mark_agent_offline(
    agent_id: str,
    session: AsyncSession,
) -> bool:
    """Mark an agent as offline."""
    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalars().first()

    if not agent:
        return False

    agent.status = "offline"
    await session.flush()
    logger.info("Agent marked offline: agent_id=%s", agent_id)
    return True


async def mark_agent_error(
    agent_id: str,
    error_message: str,
    session: AsyncSession,
) -> bool:
    """Mark an agent as in error state."""
    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalars().first()

    if not agent:
        return False

    agent.status = "error"
    agent.error_message = error_message
    await session.flush()
    logger.error("Agent marked in error state: agent_id=%s error=%s", agent_id, error_message)
    return True
