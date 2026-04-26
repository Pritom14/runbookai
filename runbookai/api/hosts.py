"""Host credential management API.

Allows operators to register per-host SSH credentials so the agent can
connect to remote servers during incident response.

Endpoints:
  POST   /api/hosts          — register or update credentials for a host
  GET    /api/hosts          — list all registered hosts (no keys exposed)
  DELETE /api/hosts/{hostname} — remove credentials for a host
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from runbookai.database import get_session
from runbookai.models import HostCredential

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


class HostCredentialIn(BaseModel):
    hostname: str = Field(..., description="Hostname or IP of the remote server")
    username: str = Field(..., description="SSH login username")
    private_key_pem: Optional[str] = Field(
        None,
        description="PEM-encoded SSH private key. Leave null to use the global key from settings.",
    )
    port: int = Field(22, ge=1, le=65535)


class HostCredentialOut(BaseModel):
    id: str
    hostname: str
    username: str
    port: int
    has_private_key: bool

    model_config = {"from_attributes": True}


@router.post("", response_model=HostCredentialOut, status_code=201)
async def register_host(body: HostCredentialIn, session: Any = Depends(get_session)) -> Any:
    """Register or update SSH credentials for a host."""
    result = await session.execute(
        select(HostCredential).where(HostCredential.hostname == body.hostname)
    )
    row: HostCredential | None = result.scalar_one_or_none()

    if row is None:
        row = HostCredential(
            hostname=body.hostname,
            username=body.username,
            private_key_pem=body.private_key_pem,
            port=body.port,
        )
        session.add(row)
    else:
        row.username = body.username
        row.port = body.port
        if body.private_key_pem is not None:
            row.private_key_pem = body.private_key_pem

    await session.commit()
    await session.refresh(row)
    return HostCredentialOut(
        id=row.id,
        hostname=row.hostname,
        username=row.username,
        port=row.port,
        has_private_key=bool(row.private_key_pem),
    )


@router.get("", response_model=list[HostCredentialOut])
async def list_hosts(session: Any = Depends(get_session)) -> Any:
    """List registered hosts. Private keys are never returned."""
    result = await session.execute(select(HostCredential))
    rows = result.scalars().all()
    return [
        HostCredentialOut(
            id=r.id,
            hostname=r.hostname,
            username=r.username,
            port=r.port,
            has_private_key=bool(r.private_key_pem),
        )
        for r in rows
    ]


@router.delete("/{hostname}", status_code=204)
async def delete_host(hostname: str, session: Any = Depends(get_session)) -> None:
    """Remove SSH credentials for a host."""
    result = await session.execute(
        select(HostCredential).where(HostCredential.hostname == hostname)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Host '{hostname}' not found")
    await session.execute(delete(HostCredential).where(HostCredential.hostname == hostname))
    await session.commit()
