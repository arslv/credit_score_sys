"""T12: клиенты — история обращений (ТЗ, «Клиенты»). ПД — только
risk_manager/compliance; admin и viewer — обезличенный вид (ваше уточнение).
T23: ролей три — персданные видит admin, viewer — обезличенно."""

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Application, Client, Employee
from app.security.deps import has_full_pii, require_roles
from app.security.pagination import Page, PageParams

router = APIRouter(prefix="/api/clients", tags=["clients"])

_VIEW_ROLES = ("admin", "operator", "viewer")  # T23: operator — бывшие права compliance
_MASKED_NAME = "Клиент скрыт"


class ClientListItem(BaseModel):
    id: UUID
    name: str
    applications_count: int


class ClientApplicationItem(BaseModel):
    id: UUID
    external_id: str
    number: int
    status: str
    submitted_at: datetime


class ClientDetail(BaseModel):
    id: UUID
    name: str
    birth_date: date | None
    phone: str | None
    applications: list[ClientApplicationItem]


def _name(client: Client, full_pii: bool) -> str:
    if not full_pii:
        return _MASKED_NAME
    return f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip()


@router.get("", response_model=Page[ClientListItem])
async def list_clients(
    params: PageParams = Depends(),
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[ClientListItem]:
    full_pii = has_full_pii(employee)
    total = len((await session.execute(select(Client.id))).scalars().all())
    clients = (
        await session.execute(
            select(Client).order_by(Client.created_at.desc()).offset(params.offset).limit(params.page_size)
        )
    ).scalars().all()

    items = []
    for client in clients:
        count = len(
            (await session.execute(select(Application.id).where(Application.client_id == client.id)))
            .scalars().all()
        )
        items.append(ClientListItem(id=client.id, name=_name(client, full_pii), applications_count=count))

    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/{client_id}", response_model=ClientDetail)
async def get_client(
    client_id: UUID,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ClientDetail:
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="клиент не найден")

    full_pii = has_full_pii(employee)
    applications = (
        await session.execute(
            select(Application).where(Application.client_id == client_id).order_by(Application.submitted_at.desc())
        )
    ).scalars().all()

    return ClientDetail(
        id=client.id, name=_name(client, full_pii),
        birth_date=client.birth_date if full_pii else None,
        phone=client.phone if full_pii else None,
        applications=[
            ClientApplicationItem(
                id=a.id, external_id=a.external_id, number=a.number, status=a.status, submitted_at=a.submitted_at,
            )
            for a in applications
        ],
    )
