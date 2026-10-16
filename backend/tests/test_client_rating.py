"""T23: рейтинг клиента — скоркарта заказчика («Рейтинг клиента.docx»).

Правила берутся из настоящего config/rulesets/dev.yaml: тест проверяет и сам
расчёт, и что конфиг описывает документ заказчика (возраст, контакты, работа,
прописка, автозапрос поручителя). Плюс сквозное: контакты из /review доходят
до снимка анкеты, рейтинг — до карточки (с ПД только ролям с доступом)."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.config import get_settings
from app.engine.client_rating import compute_client_rating
from app.engine.schema import load_ruleset_from_yaml, parse_ruleset
from app.engine.versions import save_ruleset_version
from app.models import Application, ExternalSnapshot, OcrResult

AS_OF = date(2026, 10, 1)
MAKHACHKALA = "Республика Дагестан, г. Махачкала, ул. Учебная, д. 1, кв. 2"


@pytest.fixture(scope="module")
def config():
    rating = load_ruleset_from_yaml(get_settings().ruleset_path).client_rating
    assert rating is not None, "в dev.yaml нет раздела client_rating"
    return rating


def _born(age: int) -> date:
    return date(AS_OF.year - age, 3, 1)


def _rate(config, *, age=45, address=MAKHACHKALA, stage_b=None, financed=Decimal("40000")):
    return compute_client_rating(
        config, birth_date=_born(age) if age is not None else None, as_of=AS_OF,
        registration_address=address, stage_b=stage_b, financed_amount=financed,
    )


def _points(rating, criterion):
    return next(i for i in rating.items if i.criterion == criterion).points


def _stage_b(*, relations=(), employer="", position="", employment_type="employed", guarantor=None, category=None):
    return {
        "employment": {"type": employment_type, "employer": employer, "position": position},
        "contacts": [{"relation": r} for r in relations],
        "guarantor": guarantor,
        "deal": {"goods_category_code": category},
    }


@pytest.mark.parametrize(
    ("age", "points"),
    [(21, -500), (23, -20), (26, 0), (29, 30), (33, 60), (37, 90), (45, 100), (60, 60), (68, 0)],
)
def test_age_bands_from_customer_document(config, age, points):
    assert _points(_rate(config, age=age), "age") == points


def test_under_21_zero_with_guarantor_and_guarantor_reason(config):
    alone = _rate(config, age=21, stage_b=_stage_b())
    assert _points(alone, "age") == -500
    assert [r.reason for r in alone.guarantor_reasons] == ["Возраст 21 год"]
    assert not alone.guarantor_reasons[0].satisfied

    with_guarantor = _rate(config, age=21, stage_b=_stage_b(guarantor={"last_name": "Х"}))
    assert _points(with_guarantor, "age") == 0
    assert with_guarantor.guarantor_reasons[0].satisfied


def test_66_plus_requires_guarantor(config):
    assert [r.reason for r in _rate(config, age=67).guarantor_reasons] == ["Возраст 67 лет"]


@pytest.mark.parametrize(
    ("age", "relations", "points"),
    [
        (25, ("parent", "sibling"), 100),
        (45, ("relative",), 100),
        (35, ("friend", "colleague"), -150),
        (25, ("sibling",), -150),
        (35, ("sibling",), 0),
    ],
)
def test_contacts_rules_first_match(config, age, relations, points):
    assert _points(_rate(config, age=age, stage_b=_stage_b(relations=relations)), "contacts") == points


def test_contacts_missing_is_not_counted(config):
    rating = _rate(config, stage_b=_stage_b(relations=()))
    assert _points(rating, "contacts") is None


@pytest.mark.parametrize(
    ("employer", "position", "employment_type", "points"),
    [
        ("Школа № 12", "учительница", "employed", 300),
        ("ОМВД по району", "инспектор", "employed", 300),
        ("Сбербанк", "менеджер", "employed", 230),
        ("05.ру", "продавец", "employed", 150),
        ("Супермаркет", "кассир", "employed", 120),
        ("", "", "pensioner", 120),
        ("Кафе", "официант", "employed", 50),
        ("Такси", "таксист", "self_employed", 20),
        ("ЧОП", "охранник", "employed", 0),
        ("ООО «Ромашка»", "менеджер", "employed", 0),
    ],
)
def test_work_categories(config, employer, position, employment_type, points):
    stage_b = _stage_b(employer=employer, position=position, employment_type=employment_type)
    assert _points(_rate(config, stage_b=stage_b), "work") == points


def test_work_unknown_without_text_is_not_counted(config):
    assert _points(_rate(config, stage_b=_stage_b()), "work") is None


@pytest.mark.parametrize(
    ("address", "points"),
    [
        (MAKHACHKALA, 100),
        ("Республика Дагестан, г. Кизляр, ул. Примерная, 3", 80),
        ("Республика Дагестан, Кизлярский р-н, с. Новое", 80),
        ("Республика Дагестан, Дахадаевский р-н", 60),
        ("Республика Дагестан, Сулейман-Стальский р-н", 50),
        ("Республика Дагестан, Ахтынский р-н", -50),
        ("Республика Дагестан, Гунибский р-н", -100),
        ("Республика Дагестан, Хунзахский р-н", -200),
    ],
)
def test_registration_groups(config, address, points):
    assert _points(_rate(config, address=address), "registration") == points


def test_mountain_district_and_refused_village_request_guarantor(config):
    mountain = _rate(config, address="Республика Дагестан, Цумадинский р-н, с. Агвали")
    assert any("Цумадинский" in r.reason for r in mountain.guarantor_reasons)

    refused = _rate(config, address="Республика Дагестан, г. Махачкала, пос. Ташкапур")
    assert _points(refused, "registration") is None  # отказная прописка — без баллов
    assert any("Отказная прописка" in r.reason for r in refused.guarantor_reasons)


def test_other_region_not_counted(config):
    rating = _rate(config, address="г. Москва, ул. Макетная, д. 5")
    assert _points(rating, "registration") is None


def test_young_client_expensive_phone_requests_guarantor(config):
    phone = _stage_b(relations=("parent",), category="phones")
    assert any("телефон" in r.reason for r in _rate(config, age=23, stage_b=phone, financed=Decimal("55000")).guarantor_reasons)
    assert not _rate(config, age=23, stage_b=phone, financed=Decimal("30000")).guarantor_reasons
    assert not any("телефон" in r.reason for r in _rate(config, age=30, stage_b=phone, financed=Decimal("55000")).guarantor_reasons)


def test_points_sum_and_missing_do_not_count(config):
    stage_b = _stage_b(relations=("parent", "sibling"), employer="Школа", position="учитель")
    rating = _rate(config, age=45, stage_b=stage_b)
    assert rating.total == 100 + 100 + 300 + 100
    assert rating.affects_decision is False
    assert rating.not_yet_counted  # платёжеспособность и т.п. — честно названы

    before_review = _rate(config, age=45, stage_b=None)
    assert before_review.total == 100 + 100  # возраст + прописка; контакты/работа — нет данных
    assert [i.points for i in before_review.items if i.criterion in ("contacts", "work")] == [None, None]


# --- сквозное: /review -> снимок анкеты -> карточка --------------------------------


async def _active_ruleset(session_factory):
    raw = yaml.safe_load(Path(get_settings().ruleset_path).read_text(encoding="utf-8"))
    raw["effective_from"] = (date.today() - timedelta(days=1)).isoformat()
    async with session_factory() as session:
        await save_ruleset_version(session, parse_ruleset(raw))
        await session.commit()


async def _application(session_factory, *, external_id: str, stage_b: dict | None) -> uuid.UUID:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        application = Application(
            external_id=external_id, status="to_operator", goods_name="Смартфон", goods_price=Decimal("60000"),
            down_payment=Decimal("0"), term_months=12, tariff="standard", requested_amount=Decimal("60000"),
            profile={}, consent_pd_at=now, submitted_at=now,
        )
        session.add(application)
        await session.flush()
        fields = {
            "birth_date": {"value": (date.today() - timedelta(days=45 * 366)).isoformat(), "confidence": 0.98},
            "registration_address": {"value": MAKHACHKALA, "confidence": 0.98},
        }
        session.add(OcrResult(
            application_id=application.id, provider="fake", response_raw={}, fields={"fields": fields, "flags": {}},
        ))
        if stage_b is not None:
            session.add(ExternalSnapshot(
                application_id=application.id, stage="B", payload_raw={"stage_b": stage_b}, content_hash="x" * 64,
            ))
        await session.commit()
        return application.id


async def test_review_webhook_keeps_contacts_and_employer(partner_webhook_client, session_factory, monkeypatch):
    from sqlalchemy import select

    import app.tasks.stage_b as stage_b_tasks

    monkeypatch.setattr(stage_b_tasks.process_single_application_task, "delay", lambda *a: None)
    await _application(session_factory, external_id="RATING-1", stage_b=None)
    body = {
        "external_id": "RATING-1", "event_id": str(uuid.uuid4()), "status": "on_review",
        "deal": {"goods_price": 60000, "down_payment": 0, "term_months": 12, "tariff": "standard"},
        "employment": {"type": "employed", "employer": "Школа № 12", "position": "учитель"},
        "income": {"declared_monthly": 80000}, "obligations": {},
        "bki_consent": {"given": True},
        "contacts": [{"name": "Мама", "phone": "+79990001122", "relation": "parent"}],
    }
    response = await partner_webhook_client.post(
        "/api/v1/partner/review", json=body,
        headers={"Authorization": f"Bearer {get_settings().partner_inbound_token}"},
    )
    assert response.status_code == 202, response.text

    async with session_factory() as session:
        snapshot = (await session.execute(
            select(ExternalSnapshot).where(ExternalSnapshot.stage == "B")
        )).scalars().one()
    stage_b = snapshot.payload_raw["stage_b"]
    assert stage_b["contacts"] == [{"name": "Мама", "phone": "+79990001122", "relation": "parent"}]
    assert stage_b["employment"]["employer"] == "Школа № 12"


async def test_card_shows_rating_with_values_only_for_pii_roles(admin_client, session_factory):
    from tests._admin_helpers import create_employee, login

    await _active_ruleset(session_factory)
    stage_b = _stage_b(relations=("parent", "sibling"), employer="Школа", position="учитель")
    application_id = await _application(session_factory, external_id="RATING-2", stage_b=stage_b)

    await create_employee(session_factory, email="rating-risk@example.com", role="risk_manager")
    await login(admin_client, "rating-risk@example.com")
    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200, response.text
    rating = response.json()["client_rating"]
    assert rating["total"] == 600
    assert rating["affects_decision"] is False
    assert {i["criterion"]: i["value"] for i in rating["items"]}["registration"] == "Махачкала"

    await create_employee(session_factory, email="rating-viewer@example.com", role="viewer")
    await login(admin_client, "rating-viewer@example.com")
    response = await admin_client.get(f"/api/applications/{application_id}")
    assert response.status_code == 200, response.text
    masked = response.json()["client_rating"]
    assert masked["total"] == 600
    assert all(i["value"] is None for i in masked["items"])  # баллы видны, ПД — нет
