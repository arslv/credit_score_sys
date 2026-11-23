"""Заявка, заведённая вручную в админке (решение владельца 09.11.2026):
admin и operator, с фото паспорта, отдельно от CRM — тот же путь, что у
заявки из CRM с паспортом в анкете (стадия А, затем стадия Б), но решения в
CRM не уходят."""

import json
import random
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Application, AuditLog, Decision, Document, ExternalSnapshot, GoodsCategory, OcrResult
from app.ocr.service import CRM_FORM_PROVIDER
from app.tasks.stage_a import _stage_a_then_b_from_crm_data
from tests._admin_helpers import create_employee, login
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _all_received, active_ruleset  # noqa: F401

PATH = "/api/manual-applications"
_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200
_JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200


def _form(age: int = 30, **overrides) -> dict:
    today = date.today()
    form = {
        "last_name": "Тестов", "first_name": "Ручной", "middle_name": "Заявкович", "phone": "+79990001122",
        "passport": {
            "birth_date": today.replace(year=today.year - age).isoformat(),
            "passport_series": unique_valid_passport_series(),
            "passport_number": f"{random.randint(0, 999999):06d}",
            "issue_date": today.replace(year=today.year - (age - 25)).isoformat(),
            "issued_by": "МВД по Республике Дагестан",
            "department_code": "050-001",
            "registration_address": "г. Махачкала, ул. Тестовая, д. 1",
        },
        "goods_name": "Холодильник", "goods_category_code": "household_appliances",
        "goods_price": "60000", "down_payment": "10000", "term_months": 12,
        "income": "90000", "other_credit_payments": "5000", "bki_consent": True,
        "employment": {"type": "employed", "employer": "Школа", "position": "учитель"},
    }
    form.update(overrides)
    return form


@pytest.fixture
def queued(monkeypatch):
    import app.tasks.stage_a as stage_a_tasks

    calls: list[str] = []
    monkeypatch.setattr(stage_a_tasks.process_crm_data_task, "delay", lambda app_id: calls.append(app_id))
    return calls


@pytest.fixture
def app_storage(storage):
    from app.main import app

    previous = app.state.storage
    app.state.storage = storage
    yield storage
    app.state.storage = previous


async def _as(admin_client, session_factory, role: str):
    email = f"manual-{role}-{random.randint(0, 10**9)}@example.com"
    await create_employee(session_factory, email=email, role=role)
    await login(admin_client, email)


async def _post(admin_client, form: dict, files: dict | None = None):
    return await admin_client.post(PATH, data={"data": json.dumps(form, ensure_ascii=False)}, files=files or {})


@pytest.mark.parametrize("role", ["operator", "admin"])
async def test_staff_creates_application_with_photos(
    role, admin_client, session_factory, app_storage, queued, active_ruleset,
):
    await _as(admin_client, session_factory, role)
    files = {"passport_main": ("main.png", _PNG, "image/png"), "passport_reg": ("reg.jpg", _JPEG, "image/jpeg")}

    response = await _post(admin_client, _form(), files)

    assert response.status_code == 201, response.text
    body = response.json()
    assert queued == [body["id"]]  # дальше — та же задача, что для паспорта из CRM
    async with session_factory() as session:
        application = await session.get(Application, body["id"])
        assert application.source == "manual" and application.status == "awaiting_data"
        assert application.number == body["number"]
        assert application.requested_amount == 50000
        assert application.consent_bki_at is not None
        docs = (await session.execute(select(Document).where(Document.application_id == application.id))).scalars().all()
        assert sorted((d.kind, d.mime) for d in docs) == [("passport_main", "image/png"), ("passport_reg", "image/jpeg")]
        ocr = (await session.execute(select(OcrResult).where(OcrResult.application_id == application.id))).scalar_one()
        assert ocr.provider == CRM_FORM_PROVIDER
        snapshot_b = (
            await session.execute(
                select(ExternalSnapshot).where(ExternalSnapshot.application_id == application.id, ExternalSnapshot.stage == "B")
            )
        ).scalar_one()
        assert snapshot_b.payload_raw["stage_b"]["income"] == {"declared_monthly": "90000"}
        audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "create_application", AuditLog.object_id == application.id))
        ).scalar_one()
        assert audit.details == {"number": application.number}  # без персданных

    detail = (await admin_client.get(f"/api/applications/{body['id']}")).json()
    assert detail["source"] == "manual"
    # Паспорт ещё не проверен (клиента нет) — ФИО из анкеты, не «Клиент скрыт».
    assert detail["client_name"] == "Тестов Ручной Заявкович"
    listed = (await admin_client.get("/api/applications", params={"view": "all"})).json()["items"]
    assert any(i["id"] == body["id"] and i["client_name"] == "Тестов Ручной Заявкович" for i in listed)


async def test_manual_application_runs_stages_and_sends_nothing_to_crm(
    admin_client, session_factory, app_storage, queued, partner_admin, partner_client, active_ruleset,
):
    await _as(admin_client, session_factory, "operator")
    response = await _post(admin_client, _form())  # фото необязательны
    assert response.status_code == 201, response.text
    application_id = response.json()["id"]

    # Задача Celery — здесь напрямую.
    await _stage_a_then_b_from_crm_data(session_factory, application_id, partner_client)

    async with session_factory() as session:
        application = await session.get(Application, application_id)
        stages = (
            await session.execute(select(Decision.stage).where(Decision.application_id == application.id).order_by(Decision.created_at))
        ).scalars().all()
    assert stages == ["A", "B"]
    assert application.status == "gray_zone"  # БКИ в тестах не подключено
    assert await _all_received(partner_admin, application.external_id) == []  # в CRM — ничего


async def test_viewer_cannot_create_and_bad_input_is_rejected(
    admin_client, session_factory, app_storage, queued, active_ruleset,
):
    await _as(admin_client, session_factory, "viewer")
    assert (await _post(admin_client, _form())).status_code == 403

    await _as(admin_client, session_factory, "operator")
    no_series = _form()
    no_series["passport"]["passport_series"] = "12"
    response = await _post(admin_client, no_series)
    assert response.status_code == 422 and "passport_series" in response.text

    assert (await _post(admin_client, _form(down_payment="60000"))).status_code == 422
    not_image = {"passport_main": ("main.png", b"not an image at all", "image/png")}
    assert (await _post(admin_client, _form(), not_image)).status_code == 415
    assert queued == []
    async with session_factory() as session:
        assert (await session.execute(select(Application).where(Application.source == "manual"))).first() is None


async def test_options_list_goods_categories_for_operator(admin_client, session_factory):
    async with session_factory() as session:
        session.add(GoodsCategory(code="test_manual_cat", name="Тестовая категория", sharia_allowed=True, liquidity="high"))
        await session.commit()
    await _as(admin_client, session_factory, "operator")

    response = await admin_client.get(f"{PATH}/options")

    assert response.status_code == 200
    assert {"code": "test_manual_cat", "name": "Тестовая категория"} in response.json()["goods_categories"]


# --- правка анкеты (решение владельца 16.11.2026) -------------------------------


async def test_edit_form_reruns_checks_with_new_data(
    admin_client, session_factory, app_storage, queued, partner_admin, partner_client, active_ruleset,
):
    """Сотрудник открывает анкету, исправляет — заявка возвращается на
    проверку (awaiting_data) и проходит стадии А и Б заново по новым данным;
    прежние данные остаются (новые строки, правило 1)."""
    from sqlalchemy import func

    from app.models import StatusHistory

    await _as(admin_client, session_factory, "operator")
    created = (await _post(admin_client, _form())).json()
    await _stage_a_then_b_from_crm_data(session_factory, created["id"], partner_client)

    form = (await admin_client.get(f"{PATH}/{created['id']}/form")).json()
    assert form["last_name"] == "Тестов" and form["income"] == "90000"
    assert form["passport"]["department_code"] == "050-001"
    assert form["employment"]["employer"] == "Школа"

    form["income"] = "150000"
    form["last_name"] = "Исправленов"
    response = await admin_client.post(f"{PATH}/{created['id']}", data={"data": json.dumps(form, ensure_ascii=False)})
    assert response.status_code == 200, response.text
    assert queued[-1] == created["id"]

    async with session_factory() as session:
        application = await session.get(Application, created["id"])
        assert application.status == "awaiting_data"
        last = (
            await session.execute(
                select(StatusHistory).where(StatusHistory.application_id == application.id)
                .order_by(StatusHistory.created_at.desc()).limit(1)
            )
        ).scalar_one()
        assert (last.from_status, last.to_status, last.actor_type) == ("gray_zone", "awaiting_data", "employee")
        assert (
            await session.execute(select(func.count()).select_from(OcrResult).where(OcrResult.application_id == application.id))
        ).scalar_one() == 2  # прежний паспорт не перезаписан
        assert (
            await session.execute(select(AuditLog).where(AuditLog.action == "edit_application", AuditLog.object_id == application.id))
        ).scalar_one().details == {"number": application.number}

    await _stage_a_then_b_from_crm_data(session_factory, created["id"], partner_client)
    async with session_factory() as session:
        stages = (
            await session.execute(select(Decision.stage).where(Decision.application_id == created["id"]).order_by(Decision.created_at))
        ).scalars().all()
    assert stages == ["A", "B", "A", "B"]  # проверено заново

    detail = (await admin_client.get(f"/api/applications/{created['id']}")).json()
    assert detail["client_name"].startswith("Исправленов")


async def test_edit_blocked_while_checking_and_for_viewer(
    admin_client, session_factory, app_storage, queued, active_ruleset,
):
    await _as(admin_client, session_factory, "operator")
    created = (await _post(admin_client, _form())).json()
    async with session_factory() as session:
        application = await session.get(Application, created["id"])
        application.status = "stage_b"  # идёт проверка
        await session.commit()

    busy = await admin_client.post(f"{PATH}/{created['id']}", data={"data": json.dumps(_form(), ensure_ascii=False)})
    assert busy.status_code == 409

    await _as(admin_client, session_factory, "viewer")
    assert (await admin_client.get(f"{PATH}/{created['id']}/form")).status_code == 403
    assert (await admin_client.post(f"{PATH}/{created['id']}", data={"data": "{}"})).status_code == 403


async def test_manual_application_always_has_bki_consent(
    admin_client, session_factory, app_storage, queued, active_ruleset,
):
    """Даже если форма прислала bki_consent=false (старая форма из кэша), у
    ручной заявки согласие на КИ стоит — иначе серая зона «нет согласия» без
    возможности запросить отчёт (случай на сервере 17.11.2026)."""
    await _as(admin_client, session_factory, "operator")
    response = await _post(admin_client, _form(bki_consent=False))
    assert response.status_code == 201, response.text
    async with session_factory() as session:
        application = await session.get(Application, response.json()["id"])
        assert application.consent_bki_at is not None


async def test_product_fields_and_markup_derive_cost_and_payment(
    admin_client, session_factory, app_storage, queued, active_ruleset,
):
    """Наценка, тип продукта, партнёр и точка продаж (как в карточке CRM) —
    в анкете; стоимость = цена + наценка, платёж без ввода — из наценки:
    (стоимость − взнос) / срок. При правке — возвращаются в форму."""
    await _as(admin_client, session_factory, "operator")
    form = _form(markup="45096", product_type="Товар", partner="Мах Город", sales_point="Махачкала")
    form.update(goods_price="189990", down_payment="80000", term_months=12)
    created = await _post(admin_client, form)
    assert created.status_code == 201, created.text

    async with session_factory() as session:
        snapshot_b = (await session.execute(
            select(ExternalSnapshot).where(
                ExternalSnapshot.application_id == created.json()["id"], ExternalSnapshot.stage == "B",
            )
        )).scalar_one()
    deal = snapshot_b.payload_raw["stage_b"]["deal"]
    assert deal["markup"] == "45096"
    assert deal["total_cost"] == "235086"
    assert deal["monthly_payment"] == "12923.83"  # (235086 − 80000) / 12
    assert (deal["product_type"], deal["partner"], deal["sales_point"]) == ("Товар", "Мах Город", "Махачкала")

    back = (await admin_client.get(f"{PATH}/{created.json()['id']}/form")).json()
    assert (back["markup"], back["partner"], back["sales_point"], back["product_type"]) == (
        "45096", "Мах Город", "Махачкала", "Товар",
    )


async def test_list_shows_sales_point_and_anket_count(admin_client, session_factory, app_storage, queued, active_ruleset):
    """Список как в CRM: точка продаж, сколько анкет (заявитель + поручитель),
    время решения (пока решения нет — пусто)."""
    await _as(admin_client, session_factory, "operator")
    form = _form(sales_point="Махачкала, Ленина 1")
    form["guarantor"] = {"last_name": "Поручителев", "first_name": "Пётр", "phone": "+79990001122"}
    created = await _post(admin_client, form)
    assert created.status_code == 201, created.text

    listed = await admin_client.get("/api/applications", params={"view": "all", "page_size": 100})
    assert listed.status_code == 200, listed.text
    item = next(i for i in listed.json()["items"] if i["id"] == created.json()["id"])
    assert item["sales_point"] == "Махачкала, Ленина 1"
    assert item["anket_count"] == 2
    assert item["decided_at"] is None
