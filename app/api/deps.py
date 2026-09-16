from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Tenant
from app.db.session import SessionLocal


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def get_tenant_id(
    db: Annotated[Session, Depends(get_db)],
    x_tenant_id: Annotated[int | None, Header(ge=1)] = None,
) -> int:
    tenant_id = x_tenant_id or get_settings().default_tenant_id
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(404, f"tenant {tenant_id} not found")
    return tenant_id