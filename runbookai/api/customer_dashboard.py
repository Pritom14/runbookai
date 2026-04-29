"""Customer dashboard endpoints for cloud SaaS.

Provides a unified view of customer incidents, agents, and system activity.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import and_, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import Agent, Customer, Incident, IncidentStatus

logger = logging.getLogger("runbookai.api.customer_dashboard")
router = APIRouter(prefix="/api/customers", tags=["dashboard"])


class IncidentSummary(BaseModel):
    id: str
    alert_name: str
    status: str
    source: str
    created_at: str
    resolved_at: Optional[str]


class AgentInfo(BaseModel):
    agent_id: str
    name: str
    status: str
    last_heartbeat: Optional[str]


class DashboardResponse(BaseModel):
    customer_id: str
    email: str
    incidents: list[IncidentSummary]
    agent: Optional[AgentInfo]
    stats: dict


@router.get("/{customer_id}/dashboard", response_model=DashboardResponse)
async def get_customer_dashboard(
    customer_id: str,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
) -> DashboardResponse:
    """Get customer dashboard with incidents, agent status, and stats.

    Args:
        customer_id: Customer ID
        days: Number of days of history to include (default 30)
        x_api_key: API key for authentication

    Returns:
        Dashboard with incidents, agent status, and usage statistics
    """
    # Verify customer exists and API key matches
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Verify API key if provided
    if x_api_key and x_api_key != customer.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Get recent incidents (last 30 days)
    cutoff = datetime.utcnow() - timedelta(days=days)
    incidents_result = await session.execute(
        select(Incident)
        .where(
            and_(
                Incident.customer_id == customer_id,
                Incident.created_at >= cutoff,
            )
        )
        .order_by(desc(Incident.created_at))
        .limit(100)
    )
    incidents = incidents_result.scalars().all()

    # Get agent status
    agent_result = await session.execute(
        select(Agent)
        .where(Agent.customer_id == customer_id)
        .order_by(desc(Agent.last_heartbeat))
        .limit(1)
    )
    agent = agent_result.scalars().first()

    agent_info = None
    if agent:
        agent_info = AgentInfo(
            agent_id=agent.id,
            name=agent.name,
            status=agent.status,
            last_heartbeat=agent.last_heartbeat.isoformat() if agent.last_heartbeat else None,
        )

    # Calculate statistics
    total_incidents = len(incidents)
    resolved_count = sum(1 for i in incidents if i.status == IncidentStatus.RESOLVED)
    escalated_count = sum(1 for i in incidents if i.status == IncidentStatus.ESCALATED)
    pending_count = sum(1 for i in incidents if i.status == IncidentStatus.PENDING)
    avg_resolution_time = 0.0

    if resolved_count > 0:
        resolution_times = []
        for incident in incidents:
            if incident.status == IncidentStatus.RESOLVED and incident.resolved_at:
                duration = (incident.resolved_at - incident.created_at).total_seconds()
                resolution_times.append(duration)
        if resolution_times:
            avg_resolution_time = sum(resolution_times) / len(resolution_times)

    return DashboardResponse(
        customer_id=customer_id,
        email=customer.email,
        incidents=[
            IncidentSummary(
                id=i.id,
                alert_name=i.alert_name,
                status=i.status,
                source=i.source,
                created_at=i.created_at.isoformat(),
                resolved_at=i.resolved_at.isoformat() if i.resolved_at else None,
            )
            for i in incidents
        ],
        agent=agent_info,
        stats={
            "total_incidents": total_incidents,
            "resolved": resolved_count,
            "escalated": escalated_count,
            "pending": pending_count,
            "avg_resolution_time_seconds": avg_resolution_time,
            "period_days": days,
        },
    )


@router.get("/{customer_id}/incidents")
async def get_customer_incidents(
    customer_id: str,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    x_api_key: str = Header(default=""),
) -> dict:
    """Get all incidents for a customer.

    Paginated results for the customer dashboard.
    """
    # Verify customer and API key
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if x_api_key and x_api_key != customer.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Get paginated incidents
    incidents_result = await session.execute(
        select(Incident)
        .where(Incident.customer_id == customer_id)
        .order_by(desc(Incident.created_at))
        .limit(limit)
        .offset(offset)
    )
    incidents = incidents_result.scalars().all()

    return {
        "customer_id": customer_id,
        "incidents": [
            {
                "id": i.id,
                "alert_name": i.alert_name,
                "status": i.status,
                "source": i.source,
                "created_at": i.created_at.isoformat(),
                "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            }
            for i in incidents
        ],
        "limit": limit,
        "offset": offset,
    }
