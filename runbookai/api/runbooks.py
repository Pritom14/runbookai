"""Runbook CRUD endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from runbookai.database import get_session
from runbookai.models import Runbook

logger = logging.getLogger("runbookai.api.runbooks")
router = APIRouter(prefix="/runbooks", tags=["runbooks"])


class RunbookCreate(BaseModel):
    name: str
    alert_pattern: str
    content: str


@router.post("", status_code=201)
async def create_runbook(body: RunbookCreate, session: AsyncSession = Depends(get_session)):
    rb = Runbook(name=body.name, alert_pattern=body.alert_pattern, content=body.content)
    session.add(rb)
    await session.commit()
    await session.refresh(rb)
    logger.info("runbook created: id=%s name=%s", rb.id, rb.name)
    return _serialize(rb)


@router.get("")
async def list_runbooks(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Runbook).order_by(Runbook.created_at.desc()))
    runbooks = result.scalars().all()
    return {"runbooks": [_serialize(rb) for rb in runbooks]}


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str, session: AsyncSession = Depends(get_session)):
    rb = await session.get(Runbook, runbook_id)
    if rb is None:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return _serialize(rb)


@router.delete("/{runbook_id}", status_code=204)
async def delete_runbook(runbook_id: str, session: AsyncSession = Depends(get_session)):
    rb = await session.get(Runbook, runbook_id)
    if rb is None:
        raise HTTPException(status_code=404, detail="Runbook not found")
    await session.delete(rb)
    await session.commit()
    logger.info("runbook deleted: id=%s", runbook_id)


def _serialize(rb: Runbook) -> dict:
    return {
        "id": rb.id,
        "name": rb.name,
        "alert_pattern": rb.alert_pattern,
        "content": rb.content,
        "created_at": rb.created_at,
    }
