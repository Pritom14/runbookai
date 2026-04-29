"""Cloud authentication middleware and utilities."""

import logging
from typing import Optional

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.models import Customer

logger = logging.getLogger("runbookai.cloud.auth")


async def get_customer_from_api_key(
    api_key: str,
    session: AsyncSession,
) -> Optional[Customer]:
    """Look up a customer by API key.

    Returns None if not found or API key is empty.
    """
    if not api_key:
        return None

    result = await session.execute(
        select(Customer).where(Customer.api_key == api_key)
    )
    return result.scalars().first()


async def verify_api_key(
    x_api_key: str = Header(default=""),
    session: Optional[AsyncSession] = None,
) -> str:
    """Extract and validate API key from request header.

    Raises HTTPException(401) if API key is missing or invalid.
    Returns the API key if valid.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header",
        )

    if session:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer:
            logger.warning("Invalid API key attempted")
            raise HTTPException(
                status_code=401,
                detail="Invalid API key",
            )

    return x_api_key


async def get_customer_from_header(
    x_api_key: str = Header(default=""),
    session: Optional[AsyncSession] = None,
) -> Optional[Customer]:
    """Extract customer from X-API-Key header.

    Returns None if header is missing.
    Raises HTTPException(401) if API key is invalid.
    """
    if not x_api_key:
        return None

    if session:
        customer = await get_customer_from_api_key(x_api_key, session)
        if not customer:
            logger.warning("Invalid API key attempted")
            raise HTTPException(
                status_code=401,
                detail="Invalid API key",
            )
        return customer

    return None
