"""T12: справочники — категории товаров, причины, чёрный список (ТЗ,
«Справочники»: меняют risk_manager и admin)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Blacklist, Employee, GoodsCategory, ReasonCode
from app.security.audit import record_audit
from app.security.deps import require_roles
from app.security.pagination import Page, PageParams

router = APIRouter(prefix="/api/references", tags=["references"])

_VIEW_ROLES = ("risk_manager", "admin")


# --- категории товаров -----------------------------------------------------------


class GoodsCategoryOut(BaseModel):
    id: UUID
    code: str
    name: str
    sharia_allowed: bool
    liquidity: str
    active: bool


class GoodsCategoryIn(BaseModel):
    code: str
    name: str
    sharia_allowed: bool
    liquidity: str
    active: bool = True


@router.get("/goods-categories", response_model=Page[GoodsCategoryOut])
async def list_goods_categories(
    params: PageParams = Depends(),
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[GoodsCategoryOut]:
    total = len((await session.execute(select(GoodsCategory.id))).scalars().all())
    rows = (
        await session.execute(select(GoodsCategory).order_by(GoodsCategory.name).offset(params.offset).limit(params.page_size))
    ).scalars().all()
    return Page(
        items=[
            GoodsCategoryOut(
                id=r.id, code=r.code, name=r.name, sharia_allowed=r.sharia_allowed, liquidity=r.liquidity,
                active=r.active,
            )
            for r in rows
        ],
        total=total, page=params.page, page_size=params.page_size,
    )


@router.post("/goods-categories", response_model=GoodsCategoryOut, status_code=status.HTTP_201_CREATED)
async def create_goods_category(
    payload: GoodsCategoryIn, request: Request,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> GoodsCategoryOut:
    row = GoodsCategory(**payload.model_dump())
    session.add(row)
    await session.flush()
    await record_audit(
        session, employee_id=employee.id, action="manage_reference",
        object_type="goods_category", object_id=row.id, request=request,
    )
    await session.commit()
    return GoodsCategoryOut(
        id=row.id, code=row.code, name=row.name, sharia_allowed=row.sharia_allowed, liquidity=row.liquidity,
        active=row.active,
    )


@router.patch("/goods-categories/{category_id}", response_model=GoodsCategoryOut)
async def update_goods_category(
    category_id: UUID, payload: GoodsCategoryIn, request: Request,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> GoodsCategoryOut:
    row = await session.get(GoodsCategory, category_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="категория не найдена")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    await record_audit(
        session, employee_id=employee.id, action="manage_reference",
        object_type="goods_category", object_id=row.id, request=request,
    )
    await session.commit()
    return GoodsCategoryOut(
        id=row.id, code=row.code, name=row.name, sharia_allowed=row.sharia_allowed, liquidity=row.liquidity,
        active=row.active,
    )


# --- причины -----------------------------------------------------------------------


class ReasonCodeOut(BaseModel):
    code: str
    kind: str
    title: str
    active: bool


@router.get("/reason-codes", response_model=Page[ReasonCodeOut])
async def list_reason_codes(
    params: PageParams = Depends(),
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[ReasonCodeOut]:
    total = len((await session.execute(select(ReasonCode.code))).scalars().all())
    rows = (
        await session.execute(select(ReasonCode).order_by(ReasonCode.code).offset(params.offset).limit(params.page_size))
    ).scalars().all()
    return Page(
        items=[ReasonCodeOut(code=r.code, kind=r.kind, title=r.title, active=r.active) for r in rows],
        total=total, page=params.page, page_size=params.page_size,
    )


# --- чёрный список -------------------------------------------------------------------


class BlacklistOut(BaseModel):
    id: UUID
    last_name: str | None
    first_name: str | None
    reason: str
    active: bool


class BlacklistIn(BaseModel):
    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    birth_date: date | None = None
    passport_series: str | None = None
    passport_number: str | None = None
    phone: str | None = None
    reason: str


@router.get("/blacklist", response_model=Page[BlacklistOut])
async def list_blacklist(
    params: PageParams = Depends(),
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[BlacklistOut]:
    total = len((await session.execute(select(Blacklist.id))).scalars().all())
    rows = (
        await session.execute(select(Blacklist).order_by(Blacklist.added_at.desc()).offset(params.offset).limit(params.page_size))
    ).scalars().all()
    return Page(
        items=[
            BlacklistOut(id=r.id, last_name=r.last_name, first_name=r.first_name, reason=r.reason, active=r.active)
            for r in rows
        ],
        total=total, page=params.page, page_size=params.page_size,
    )


@router.post("/blacklist", response_model=BlacklistOut, status_code=status.HTTP_201_CREATED)
async def add_to_blacklist(
    payload: BlacklistIn, request: Request,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> BlacklistOut:
    row = Blacklist(**payload.model_dump(), added_by=employee.id)
    session.add(row)
    await session.flush()
    await record_audit(
        session, employee_id=employee.id, action="manage_reference",
        object_type="blacklist", object_id=row.id, request=request,
    )
    await session.commit()
    return BlacklistOut(id=row.id, last_name=row.last_name, first_name=row.first_name, reason=row.reason, active=row.active)
