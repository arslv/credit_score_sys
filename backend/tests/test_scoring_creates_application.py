"""«Рассмотреть» по заявке, которой у нас нет (решение владельца 20.11.2026):
/new-order не дошёл (CRM слала его на старый адрес) — /scoring сам создаёт
заявку из своих данных и паспорта applicant, дальше обычный путь."""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models import Application, ExternalSnapshot, OcrResult, PartnerInboundEvent, StatusHistory
from tests._crm import SCORING_PATH, crm_headers, new_order_id, scoring_body_from_internal
from tests.test_engine_pipeline import active_ruleset  # noqa: F401
from tests.test_engine_stage_b import _DEFAULT_STAGE_B_PAYLOAD
from tests.test_ocr_toggle import _applicant


async def _post(body: dict):
    from app.main import app as fastapi_app

    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        return await client.post(SCORING_PATH, json=body, headers=crm_headers())


async def test_scoring_for_unknown_order_creates_application(session_factory, active_ruleset):
    external_id = new_order_id()
    applicant = {**_applicant(), "last_name": "Безновый", "first_name": "Тест", "middle_name": "Тестович"}
    body = scoring_body_from_internal(external_id, _DEFAULT_STAGE_B_PAYLOAD, applicant=applicant)

    with patch("app.tasks.stage_a.process_crm_data_task.delay") as delay:
        response = await _post(body)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "accepted"}

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        # Дальше — как заявка, ждавшая паспорт: сделка из /scoring, паспорт — в OCR-результате.
        assert application.status == "awaiting_data"
        assert application.profile["last_name"] == "Безновый"
        assert application.goods_price > 0 and application.term_months > 0
        history = (
            await session.execute(select(StatusHistory.to_status).where(StatusHistory.application_id == application.id))
        ).scalars().all()
        assert history == ["awaiting_data"]
        stages = (
            await session.execute(select(ExternalSnapshot.stage).where(ExternalSnapshot.application_id == application.id))
        ).scalars().all()
        assert sorted(stages) == ["A", "B"]
        assert (
            await session.execute(select(func.count()).select_from(OcrResult).where(OcrResult.application_id == application.id))
        ).scalar_one() == 1
        assert (
            await session.execute(
                select(func.count()).select_from(PartnerInboundEvent)
                .where(PartnerInboundEvent.external_id == external_id)
            )
        ).scalar_one() == 1
    delay.assert_called_once_with(str(application.id))


async def test_scoring_for_unknown_order_without_passport_creates_nothing(session_factory, active_ruleset):
    external_id = new_order_id()
    body = scoring_body_from_internal(external_id, _DEFAULT_STAGE_B_PAYLOAD, applicant=None)

    with patch("app.tasks.stage_a.process_crm_data_task.delay") as delay:
        response = await _post(body)
    assert response.status_code == 422, response.text
    delay.assert_not_called()

    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(Application).where(Application.external_id == external_id)
            )
        ).scalar_one()
        assert count == 0


async def test_new_order_without_photos_accepted_when_ocr_disabled(session_factory, active_ruleset):
    """Распознавание выключено — фото не нужны: /new-order без фото создаёт
    заявку (25.11.2026: CRM получала 422, заявка не создавалась)."""
    from app.main import app as fastapi_app
    from tests._crm import NEW_ORDER_PATH, new_order_body
    from tests.test_ocr_toggle import _disable_ocr

    await _disable_ocr(session_factory)
    external_id = new_order_id()
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="https://test") as client:
        response = await client.post(
            NEW_ORDER_PATH, json=new_order_body(external_id, photos=[]), headers=crm_headers(),
        )
    assert response.status_code == 200, response.text
    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one()
        assert application.status == "awaiting_data"
