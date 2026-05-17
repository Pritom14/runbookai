"""Agent lifecycle management endpoints.

Provides endpoints for agents to register, heartbeat, and manage connections
with the cloud orchestrator.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import Agent, Customer

logger = logging.getLogger("runbookai.api.agents")
router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentConnectRequest(BaseModel):
    name: str  # Agent name (e.g., "prod-agent-01")


class AgentConnectResponse(BaseModel):
    agent_id: str
    customer_id: str
    status: str


class AgentStatusResponse(BaseModel):
    status: str  # "online" | "offline" | "error" | "no_agent"
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    last_heartbeat: Optional[str] = None
    error_message: Optional[str] = None
    message: Optional[str] = None


@router.post("/{customer_id}/connect", response_model=AgentConnectResponse)
async def connect_agent(
    customer_id: str,
    request: AgentConnectRequest,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
) -> AgentConnectResponse:
    """Register agent and mark as online.

    Called by agent when starting up. Creates an Agent record if needed
    and marks it as online.

    Args:
        customer_id: Customer ID (from cloud registration)
        request: Agent details (name)
        x_api_key: API key for authentication

    Returns:
        Agent ID and connection status
    """
    # Verify customer exists
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Verify API key matches customer
    if not x_api_key or x_api_key != customer.api_key:
        logger.warning(
            "Agent connection with invalid API key: customer_id=%s",
            customer_id,
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check if agent with this name already exists
    agent_result = await session.execute(
        select(Agent).where(
            Agent.customer_id == customer_id,
            Agent.name == request.name,
        )
    )
    agent = agent_result.scalars().first()

    if not agent:
        # Create new agent
        agent = Agent(
            id=str(uuid.uuid4()),
            customer_id=customer_id,
            name=request.name,
            status="online",
            last_heartbeat=datetime.utcnow(),
        )
        session.add(agent)
    else:
        # Update existing agent
        agent.status = "online"
        agent.last_heartbeat = datetime.utcnow()
        agent.error_message = None

    await session.commit()
    await session.refresh(agent)

    logger.info(
        "Agent connected: agent_id=%s customer_id=%s name=%s",
        agent.id,
        customer_id,
        request.name,
    )

    return AgentConnectResponse(
        agent_id=agent.id,
        customer_id=customer_id,
        status="online",
    )


@router.post("/{customer_id}/heartbeat")
async def agent_heartbeat(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
) -> dict:
    """Send heartbeat to keep agent connection alive.

    Should be called every 30 seconds by the agent.
    Updates last_heartbeat timestamp.
    """
    # Verify customer exists and API key matches
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not x_api_key or x_api_key != customer.api_key:
        logger.warning(
            "Heartbeat with invalid API key: customer_id=%s",
            customer_id,
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Find the most recent agent for this customer
    agent_result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id)
        .order_by(Agent.last_heartbeat.desc())
        .limit(1)
    )
    agent = agent_result.scalars().first()

    if not agent:
        logger.warning(
            "Heartbeat from unknown agent: customer_id=%s",
            customer_id,
        )
        raise HTTPException(status_code=404, detail="Agent not found")

    # Update heartbeat
    agent.last_heartbeat = datetime.utcnow()
    agent.status = "online"
    agent.error_message = None
    await session.commit()

    logger.debug("Heartbeat received: agent_id=%s", agent.id)

    return {
        "status": "ok",
        "agent_id": agent.id,
        "timestamp": agent.last_heartbeat.isoformat(),
    }


@router.get("/{customer_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentStatusResponse:
    """Get agent connection status for a customer.

    Returns the status of the customer's primary agent (most recently active).
    """
    # Verify customer exists
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Find the most recent agent
    agent_result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id)
        .order_by(Agent.last_heartbeat.desc())
        .limit(1)
    )
    agent = agent_result.scalars().first()

    if not agent:
        return AgentStatusResponse(
            status="no_agent",
            message="No agent registered for this customer",
        )

    # Check if agent is still online (heartbeat within 2 minutes)
    if agent.last_heartbeat:
        last_heartbeat_age = datetime.utcnow() - agent.last_heartbeat
        if last_heartbeat_age > timedelta(minutes=2):
            agent.status = "offline"

    return AgentStatusResponse(
        status=agent.status,
        agent_id=agent.id,
        agent_name=agent.name,
        last_heartbeat=agent.last_heartbeat.isoformat() if agent.last_heartbeat else None,
        error_message=agent.error_message,
    )


@router.post("/{customer_id}/disconnect")
async def disconnect_agent(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
) -> dict:
    """Gracefully disconnect agent.

    Called by agent before shutdown. Marks agent as offline.
    """
    # Verify customer and API key
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not x_api_key or x_api_key != customer.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Find the most recent agent
    agent_result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id)
        .order_by(Agent.last_heartbeat.desc())
        .limit(1)
    )
    agent = agent_result.scalars().first()

    if agent:
        agent.status = "offline"
        await session.commit()
        logger.info("Agent disconnected: agent_id=%s", agent.id)

    return {"status": "ok"}


@router.get("/{customer_id}")
async def list_customer_agents(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List all agents registered for a customer."""
    # Verify customer exists
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Get all agents for this customer
    agents_result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id)
        .order_by(Agent.last_heartbeat.desc())
    )
    agents = agents_result.scalars().all()

    return {
        "customer_id": customer_id,
        "agents": [
            {
                "agent_id": agent.id,
                "name": agent.name,
                "status": agent.status,
                "last_heartbeat": (
                    agent.last_heartbeat.isoformat() if agent.last_heartbeat else None
                ),
                "created_at": agent.created_at.isoformat(),
            }
            for agent in agents
        ],
    }
