"""Customer management endpoints for cloud SaaS."""

import logging
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.cloud.auth import get_customer_from_api_key
from runbookai.database import get_session
from runbookai.models import Customer

logger = logging.getLogger("runbookai.api.customers")
router = APIRouter(prefix="/api/customers", tags=["customers"])


class CustomerRegistrationRequest(BaseModel):
    email: EmailStr


class CustomerRegistrationResponse(BaseModel):
    customer_id: str
    api_key: str
    email: str
    created_at: str
    agent_setup_instructions: str


@router.post("/register", response_model=CustomerRegistrationResponse)
async def register_customer(
    request: CustomerRegistrationRequest,
    session: AsyncSession = Depends(get_session),
) -> CustomerRegistrationResponse:
    """Register a new cloud customer.

    Returns API key for customer's VPC agent to authenticate with the cloud.
    Includes agent setup instructions for quick onboarding.
    """
    # Generate a secure API key
    api_key = f"sk_{secrets.token_urlsafe(32)}"

    customer = Customer(
        id=str(uuid.uuid4()),
        api_key=api_key,
        email=request.email,
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)

    agent_setup_instructions = (
        f"1. Download the runbookai-agent package:\n"
        f"   pip install runbookai-agent\n\n"
        f"2. Configure the agent with your API key:\n"
        f"   runbookai config --api-key {api_key}\n\n"
        f"3. Start the agent in your VPC:\n"
        f"   runbookai agent start\n\n"
        f"The agent will connect to the cloud and wait for incidents.\n"
        f"Cloud URL: https://runbookai.cloud\n"
        f"Customer ID: {customer.id}\n"
    )

    logger.info(
        "New customer registered: customer_id=%s email=%s",
        customer.id,
        request.email,
    )

    return CustomerRegistrationResponse(
        customer_id=customer.id,
        api_key=api_key,
        email=request.email,
        created_at=customer.created_at.isoformat(),
        agent_setup_instructions=agent_setup_instructions,
    )


class CustomerInfo(BaseModel):
    customer_id: str
    email: str
    created_at: str


@router.get("/{customer_id}", response_model=CustomerInfo)
async def get_customer(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> CustomerInfo:
    """Get customer information by ID."""
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    return CustomerInfo(
        customer_id=customer.id,
        email=customer.email,
        created_at=customer.created_at.isoformat(),
    )


class TestConnectionResponse(BaseModel):
    status: str
    message: str


@router.post("/{customer_id}/test-connection", response_model=TestConnectionResponse)
async def test_agent_connection(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> TestConnectionResponse:
    """Test agent connectivity.

    Cloud sends a test incident to verify the agent is connected and responsive.
    This is a placeholder for phase 3.2 (agent lifecycle management).
    """
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # TODO: Phase 3.2 — implement actual test incident delivery via SSE
    logger.info("Test connection requested for customer=%s", customer_id)

    return TestConnectionResponse(
        status="ok",
        message=(
            "Test connection endpoint is ready. "
            "Agent lifecycle management will be implemented in Phase 3.2."
        ),
    )
