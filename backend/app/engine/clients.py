"""Привязка заявки к клиенту (T09, довершает T02: таблица clients существовала,
но её никто не заполнял). Клиент ищется после успешного OCR — сначала по
паспорту (серия+номер, точное совпадение), затем, если не найден, по
нормализованному ФИО + дате рождения (переоформление паспорта: тот же человек,
другой документ). Не найден ни там, ни там — создаётся новый."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Application, Client
from app.sources.name_normalize import full_name, normalize_name

_REJECTED_STATUSES = ("rejected_a", "rejected_b")


async def find_or_create_client(
    session: AsyncSession,
    *,
    last_name: str,
    first_name: str,
    middle_name: str | None,
    birth_date: date,
    passport_series: str,
    passport_number: str,
    phone: str,
) -> tuple[Client, bool]:
    """Возвращает (клиент, passport_changed). passport_changed=True — клиент
    найден по ФИО+ДР, но с ДРУГИМ паспортом на записи: не однофамилец, тот же
    человек с новым документом. Паспорт на записи клиента при этом НЕ
    перезаписывается (clients не входит в append-only список правила 1
    CLAUDE.md, но эта функция сознательно только INSERT — запись хранит
    паспорт, под которым клиент был впервые увиден; новая заявка просто
    привязывается к нему через applications.client_id)."""
    by_passport = (
        await session.execute(
            select(Client).where(
                Client.passport_series == passport_series, Client.passport_number == passport_number
            )
        )
    ).scalar_one_or_none()
    if by_passport is not None:
        return by_passport, False

    query_name = normalize_name(full_name(last_name, first_name, middle_name))
    same_birth_date_candidates = (
        await session.execute(select(Client).where(Client.birth_date == birth_date))
    ).scalars().all()
    for candidate in same_birth_date_candidates:
        candidate_name = normalize_name(
            full_name(candidate.last_name, candidate.first_name, candidate.middle_name)
        )
        if candidate_name == query_name:
            return candidate, True

    client = Client(
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        birth_date=birth_date,
        passport_series=passport_series,
        passport_number=passport_number,
        phone=phone,
    )
    session.add(client)
    await session.flush()
    return client, False


async def compute_client_history_features(
    session: AsyncSession, *, client_id, exclude_application_id
) -> dict[str, bool | int]:
    """is_repeat / prior_applications_count / prior_rejections_count — по связи
    applications.client_id, без текущей заявки. is_repeat — ключ матрицы в
    dev.yaml (`{is_repeat: true, prior_defaults: 0}`), поэтому имя поля
    фиксировано; остальные два — новые, для будущих правил и симуляции."""
    statuses = (
        await session.execute(
            select(Application.status).where(
                Application.client_id == client_id, Application.id != exclude_application_id
            )
        )
    ).scalars().all()
    prior_count = len(statuses)
    prior_rejections = sum(1 for status in statuses if status in _REJECTED_STATUSES)
    return {
        "is_repeat": prior_count > 0,
        "prior_applications_count": prior_count,
        "prior_rejections_count": prior_rejections,
    }
