"""Текущий поручитель заявки: повторная анкета с тем же человеком — та же
строка (пройденный ФССП остаётся в силе), с другим — новая строка, и
текущим становится он."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.engine.guarantors import current_guarantor
from app.engine.stage_b_pipeline import _resolve_guarantor
from app.models import Application

_PERSON = {
    "last_name": "Поручителев", "first_name": "Пётр", "middle_name": "Петрович", "birth_date": "1980-02-03",
    "passport_series": "8205", "passport_number": "123456",
}


async def _application(session) -> Application:
    now = datetime.now(timezone.utc)
    application = Application(
        external_id=str(uuid.uuid4().int % 10**12), status="stage_b", goods_name="—",
        goods_price=Decimal("0"), down_payment=Decimal("0"), term_months=0, tariff="unknown",
        requested_amount=Decimal("0"), profile={}, consent_pd_at=now, submitted_at=now,
    )
    session.add(application)
    await session.flush()
    return application


async def test_same_person_reuses_row_other_person_becomes_current(session_factory):
    async with session_factory() as session:
        application = await _application(session)

        first = await _resolve_guarantor(session, application, dict(_PERSON))
        again = await _resolve_guarantor(session, application, {**_PERSON, "registration_address": "новый адрес"})
        assert again.id == first.id
        assert again.profile["registration_address"] == "новый адрес"

        other = await _resolve_guarantor(session, application, {**_PERSON, "passport_number": "654321"})
        assert other.id != first.id
        assert (await current_guarantor(session, application.id)).id == other.id
        await session.rollback()
