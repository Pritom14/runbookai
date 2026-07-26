"""Async SQLAlchemy engine, session factory, and DB initialisation."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from runbookai.config import settings
from runbookai.models import Base

logger = logging.getLogger("runbookai.database")

logger.debug("Creating async database engine: %s", settings.database_url)
engine = create_async_engine(settings.database_url, echo=False)
logger.info("Database engine created")

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create all tables on application startup."""
    logger.info("Initializing database tables")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")
