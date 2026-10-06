"""T17, шаг 1: отдельный сид для аудита скриншотами (НЕ frontend/e2e/seed.py —
тот сид общий для регрессионного набора Playwright, трогать его для
одноразового аудита не нужно). Создаёт по сотруднику на каждую роль
(включая operator, T16) и несколько представительных заявок, чтобы в
скриншотах было что показывать, а не только пустые списки.

Запуск: python3 seed.py — печатает одну строку JSON с учётными данными."""

import asyncio
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.stage_b_pipeline import STAGE_B_FEATURE_SET_VERSION
from app.engine.versions import get_active_ruleset_version
from app.integrations.adapter import _content_hash
from app.models import (
    Application,
    Check,
    Client,
    Decision,
    ExternalSnapshot,
    FeatureSnapshot,
    FsspPostDecisionAlert,
    Guarantor,
    GoodsCategory,
    ManualOverride,
    OcrResult,
    StatusHistory,
)
from app.security.passwords import hash_password

RUN = uuid.uuid4().hex[:8]
PASSWORD = "design-audit-password-123"
ROLES = ("risk_manager", "admin", "viewer", "compliance", "operator")


def _refuse_if_production() -> None:
    if get_settings().app_env == "production":
        print("APP_ENV=production — сид для аудита не должен запускаться в бою.", file=sys.stderr)
        raise SystemExit(1)


async def _seed_employees(session) -> dict:
    from app.models import Employee

    out = {}
    for role in ROLES:
        email = f"design-{role}-{RUN}@example.com"
        session.add(Employee(full_name=f"Аудит {role}", email=email, role=role, password_hash=hash_password(PASSWORD)))
        out[role] = {"email": email, "password": PASSWORD}
    await session.flush()
    return out


async def _seed_discrepancy_application(session, ruleset_row) -> dict:
    now = datetime.now(timezone.utc)
    last_name = f"Плейрайт{RUN}"
    client = Client(
        last_name=last_name, first_name="Тест", middle_name="Тестович",
        birth_date=date(1990, 1, 1), passport_series=f"{uuid.uuid4().int % 10000:04d}",
        passport_number=f"{uuid.uuid4().int % 1000000:06d}", phone="+79990000000",
    )
    session.add(client)
    await session.flush()

    application = Application(
        client_id=client.id, external_id=f"AUDIT-DISC-{RUN}", status="to_operator",
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={"last_name": last_name, "first_name": "Тест", "middle_name": "Тестович", "phone": "+79990000000"},
        consent_pd_at=now,
    )
    session.add(application)
    await session.flush()

    decision = Decision(
        application_id=application.id, stage="A", outcome="manual_review", reason_code=None,
        freeze=False, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=None,
    )
    session.add(decision)
    await session.flush()
    session.add(
        ManualOverride(
            application_id=application.id, decision_id=decision.id,
            from_outcome="manual_review", to_outcome="approve", reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
        )
    )
    return {"id": str(application.id), "external_id": application.external_id}


async def _seed_frozen_sanctions_match(session, ruleset_row) -> dict:
    """Отказ стадии А по перечню Росфинмониторинга — ПЕРВИЧНЫЙ, не повторный.
    Записи — ровно то, что пишет конвейер (app/engine/pipeline.py run_stage_a):
    сработавшее правило `sanctions` из config/rulesets/dev.yaml, проверки
    rosfinmon/blacklist, полная история статусов и снимок признаков. Без них
    карточка честно не может объяснить отказ, и тогда она врала бы."""
    now = datetime.now(timezone.utc)
    last_name = f"Санкционов{RUN}"
    client = Client(
        last_name=last_name, first_name="Опасен", middle_name="Опасенович",
        birth_date=date(1985, 6, 15), passport_series=f"{uuid.uuid4().int % 10000:04d}",
        passport_number=f"{uuid.uuid4().int % 1000000:06d}", phone="+79990000001",
    )
    session.add(client)
    await session.flush()

    application = Application(
        client_id=client.id, external_id=f"AUDIT-FREEZE-{RUN}", status="rejected_a",
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={"last_name": last_name, "first_name": "Опасен", "middle_name": "Опасенович", "phone": "+79990000001"},
        consent_pd_at=now,
    )
    session.add(application)
    await session.flush()

    for from_status, to_status in (
        (None, "received"), ("received", "ocr"), ("ocr", "stage_a"), ("stage_a", "rejected_a"),
    ):
        session.add(
            StatusHistory(application_id=application.id, from_status=from_status, to_status=to_status, actor_type="system")
        )

    session.add(
        Check(
            application_id=application.id, subject_type="applicant", source="blacklist", status="ok",
            parsed={"found": False}, started_at=now, finished_at=now, duration_ms=3,
        )
    )
    session.add(
        Check(
            application_id=application.id, subject_type="applicant", source="rosfinmon", status="hit",
            parsed={"found": True, "list": "перечень Росфинмониторинга"}, started_at=now, finished_at=now, duration_ms=4,
        )
    )

    snapshot = FeatureSnapshot(
        application_id=application.id, stage="A",
        features={"checks": {"rosfinmon": {"status": "hit"}, "blacklist": {"status": "ok"}}},
        feature_set_ver="stage_a.v4",
    )
    session.add(snapshot)
    await session.flush()

    session.add(
        Decision(
            application_id=application.id, stage="A", outcome="reject", reason_code="SANCTIONS_HIT",
            freeze=True,
            # Тот же вид, что TriggeredRule сериализуется в _serialize_triggered_rules.
            triggered_rules=[
                {
                    "id": "sanctions", "field": "checks.rosfinmon.status", "op": "eq", "expected": "hit",
                    "actual": "hit", "reason": "SANCTIONS_HIT", "action": "reject", "freeze": True,
                }
            ],
            ruleset_version_id=ruleset_row.id, feature_snapshot_id=snapshot.id,
        )
    )
    return {"id": str(application.id), "external_id": application.external_id}


async def _seed_grayzone_fssp_not_checked(session, ruleset_row) -> dict:
    """Серая зона стадии Б: балл и ПДН в норме, но ФССП по заявителю не
    проверена — одобрение недоступно без неё (fssp_required_for_approval)."""
    now = datetime.now(timezone.utc)
    last_name = f"Серозонов{RUN}"
    client = Client(
        last_name=last_name, first_name="Серый", middle_name="Серович",
        birth_date=date(1992, 9, 30), passport_series=f"{uuid.uuid4().int % 10000:04d}",
        passport_number=f"{uuid.uuid4().int % 1000000:06d}", phone="+79990000003",
    )
    session.add(client)
    await session.flush()

    category = (await session.execute(select(GoodsCategory).where(GoodsCategory.code == "furniture"))).scalar_one()
    application = Application(
        client_id=client.id, external_id=f"AUDIT-GRAY-{RUN}", status="gray_zone",
        goods_name="Диван", goods_price=Decimal("60000.00"), down_payment=Decimal("6000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("54000.00"),
        goods_category_id=category.id,
        profile={"last_name": last_name, "first_name": "Серый", "middle_name": "Серович", "phone": "+79990000003"},
        consent_pd_at=now,
    )
    session.add(application)
    await session.flush()

    for from_status, to_status in (
        (None, "received"), ("received", "ocr"), ("ocr", "stage_a"), ("stage_a", "to_operator"),
        ("to_operator", "stage_b"), ("stage_b", "gray_zone"),
    ):
        session.add(
            StatusHistory(application_id=application.id, from_status=from_status, to_status=to_status, actor_type="system")
        )

    session.add(
        Check(
            application_id=application.id, subject_type="applicant", source="bki", status="ok",
            parsed={"score": 740}, started_at=now, finished_at=now, duration_ms=5,
        )
    )
    snapshot = FeatureSnapshot(
        application_id=application.id, stage="B",
        features={"score": 740, "features": {"pti": 0.42}}, feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
    )
    session.add(snapshot)
    await session.flush()
    session.add(
        Decision(
            application_id=application.id, stage="B", outcome="manual_review", reason_code="FSSP_NOT_CHECKED",
            freeze=False, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=snapshot.id,
            score=Decimal("740"), score_source="bki_score",
        )
    )
    return {"id": str(application.id), "external_id": application.external_id}


async def _seed_full_lifecycle_application(session, ruleset_row) -> dict:
    """Одобренная заявка с поручителем, разными источниками (в т.ч. плашки
    T16), найденным долгом по ФССП ПОСЛЕ решения (предупреждение в карточке),
    историей статусов, снимками анкеты по стадиям (два одинаковых снимка
    стадии Б — проверка, что карточка их не дублирует) и распознаванием
    паспорта — для карточки заявки целиком."""
    now = datetime.now(timezone.utc)
    last_name = f"Одобрёнов{RUN}"
    client = Client(
        last_name=last_name, first_name="Хороший", middle_name="Хорошевич",
        birth_date=date(1988, 4, 12), passport_series=f"{uuid.uuid4().int % 10000:04d}",
        passport_number=f"{uuid.uuid4().int % 1000000:06d}", phone="+79990000002", gender="male",
    )
    session.add(client)
    await session.flush()

    category = (await session.execute(select(GoodsCategory).where(GoodsCategory.code == "household_appliances"))).scalar_one()
    application = Application(
        client_id=client.id, external_id=f"AUDIT-FULL-{RUN}", status="approved",
        goods_name="Холодильник", goods_price=Decimal("80000.00"), down_payment=Decimal("8000.00"),
        term_months=18, tariff="standard", requested_amount=Decimal("72000.00"),
        goods_category_id=category.id,
        profile={"last_name": last_name, "first_name": "Хороший", "middle_name": "Хорошевич", "phone": "+79990000002"},
        consent_pd_at=now,
    )
    session.add(application)
    await session.flush()

    for from_status, to_status in (
        (None, "received"), ("received", "ocr"), ("ocr", "stage_a"), ("stage_a", "to_operator"),
        ("to_operator", "stage_b"), ("stage_b", "approved"),
    ):
        session.add(
            StatusHistory(application_id=application.id, from_status=from_status, to_status=to_status, actor_type="system")
        )

    session.add(
        OcrResult(
            application_id=application.id, provider="fixture",
            response_raw={"fixture": True},
            fields={
                "fields": {
                    "last_name": {"value": last_name, "confidence": 0.98},
                    "first_name": {"value": "Хороший", "confidence": 0.99},
                    "birth_date": {"value": "1988-04-12", "confidence": 0.97},
                    "gender": {"value": "male", "confidence": 0.95},
                    "registration_address": {"value": "г. Казань, ул. Примерная, д. 2, кв. 10", "confidence": 0.9},
                },
                "flags": {"unreadable": False, "cropped": False, "name_mismatch": False},
            },
            min_confidence=Decimal("0.900"),
        )
    )

    form = {"last_name": last_name, "first_name": "Хороший", "phone": "+79990000002", "goods_name": "Холодильник"}
    stage_b_payload = {
        "form": form,
        "stage_b": {
            "deal": {"goods_category_code": "household_appliances", "goods_price": "80000", "down_payment": "8000"},
            "employment": {"type": "employed"},
            "income": {"declared_monthly": "95000"},
        },
    }
    stage_b_payload_later = {**stage_b_payload}  # тот же ответ заказчика повторно — не дублируется в карточке
    for payload, received in (
        ({"form": form}, now - timedelta(hours=2)),
        (stage_b_payload, now - timedelta(hours=1)),
        (stage_b_payload_later, now - timedelta(minutes=30)),
    ):
        stage = "B" if "stage_b" in payload else "A"
        session.add(
            ExternalSnapshot(
                application_id=application.id, stage=stage, payload_raw=payload,
                content_hash=_content_hash(payload), received_at=received,
            )
        )

    for source, status_ in (("mvd", "ok"), ("rosfinmon", "ok"), ("blacklist", "ok"), ("fns_inn", "ok")):
        session.add(
            Check(
                application_id=application.id, subject_type="applicant", source=source, status=status_,
                parsed={"ok": True} if source != "fns_inn" else {"inn": "770123456789", "found": True},
                started_at=now, finished_at=now, duration_ms=5,
            )
        )

    stage_a_snapshot = FeatureSnapshot(
        application_id=application.id, stage="A", features={"checks": {"rosfinmon": {"status": "ok"}}},
        feature_set_ver="stage_a.v4",
    )
    session.add(stage_a_snapshot)
    await session.flush()
    session.add(
        Decision(
            application_id=application.id, stage="A", outcome="manual_review", reason_code=None,
            freeze=False, triggered_rules=[], ruleset_version_id=ruleset_row.id,
            feature_snapshot_id=stage_a_snapshot.id,
        )
    )

    snapshot = FeatureSnapshot(
        application_id=application.id, stage="B",
        features={"score": 780, "features": {"pti": 0.31}}, feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
    )
    session.add(snapshot)
    await session.flush()
    decision = Decision(
        application_id=application.id, stage="B", outcome="approve", reason_code=None,
        freeze=False, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=snapshot.id,
        score=Decimal("780"), score_source="bki_score", approved_amount=Decimal("72000.00"), approved_term=18,
        offered_tariff="standard", required_down_pay=Decimal("8000.00"),
    )
    session.add(decision)
    await session.flush()

    guarantor = Guarantor(
        application_id=application.id,
        profile={
            "last_name": "Поручителев", "first_name": "Пётр", "middle_name": "Петрович",
            "birth_date": "1985-02-20", "registration_address": "г. Тестоград, ул. Примерная, д. 2",
        },
        status="used",
    )
    session.add(guarantor)
    await session.flush()

    fssp_check = Check(
        application_id=application.id, subject_type="applicant", source="fssp", status="hit",
        parsed={
            "productions": [{"subject": "взыскание по кредитному договору", "amount": "60000"}],
            "fssp_credit_debt_amount": 60000.0, "fssp_total_amount": 60000.0, "fssp_count": 1,
        },
        started_at=now, finished_at=now, duration_ms=5,
    )
    session.add(fssp_check)
    await session.flush()
    session.add(
        FsspPostDecisionAlert(
            application_id=application.id, subject_type="applicant",
            check_id=fssp_check.id, decision_id=decision.id,
        )
    )

    return {"id": str(application.id), "external_id": application.external_id}


async def main() -> None:
    _refuse_if_production()
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            ruleset_row = await get_active_ruleset_version(session)
            if ruleset_row is None:
                raise RuntimeError("нет активной версии конфига правил")
            employees = await _seed_employees(session)
            discrepancy = await _seed_discrepancy_application(session, ruleset_row)
            frozen = await _seed_frozen_sanctions_match(session, ruleset_row)
            full = await _seed_full_lifecycle_application(session, ruleset_row)
            grayzone = await _seed_grayzone_fssp_not_checked(session, ruleset_row)
            await session.commit()
    finally:
        await engine.dispose()

    print(json.dumps({
        "employees": employees, "discrepancy": discrepancy, "frozen": frozen, "full": full, "grayzone": grayzone,
    }))


if __name__ == "__main__":
    asyncio.run(main())
