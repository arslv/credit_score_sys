"""Сиды для Playwright (T13a) — копируется в контейнер worker и запускается
там (docker compose exec), как и scripts/_e2e_runner.py: там же лежит код
приложения и есть доступ к БД. Не гоняет реальный конвейер (OCR/проверки) —
Playwright тестирует АДМИНКУ, а не бэкенд-логику (её уже покрывает pytest),
поэтому заявка с решением и расхождением вставляется в БД напрямую.

Использование: python3 seed.py — печатает одну строку JSON в stdout с
учётными данными и id для тестов.

Создаёт сотрудника с ИЗВЕСТНЫМ паролем прямо в базе, в обход входа/аудита —
ровно то, чего быть не должно в бою. Отказывается запускаться при
APP_ENV=production, тем же способом, что app/startup_guard.py для фейковых
провайдеров (ваше уточнение) — тест на это см. frontend/e2e/seed-guard.spec.ts."""

import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.versions import get_active_ruleset_version
from app.engine.stage_b_pipeline import STAGE_B_FEATURE_SET_VERSION
from app.models import (
    Application, BkiBureauCheck, BkiReport, Check, Client, Decision, Employee, FeatureSnapshot, Guarantor,
    ManualOverride,
)
from app.security.passwords import hash_password

# T22: global-setup сеет ДВЕ базы (dev и отдельную dialog_e2e) одним и тем же
# суффиксом — те же учётки входят в обе. Без переменной — случайный, как раньше.
RUN = os.environ.get("SEED_RUN") or uuid.uuid4().hex[:8]
PASSWORD = "e2e-playwright-password-123"


def _refuse_if_production() -> None:
    if get_settings().app_env == "production":
        print(
            "APP_ENV=production — сид-скрипт создаёт сотрудника с известным паролем "
            "напрямую в базе, в обход входа и аудита, и не должен запускаться в бою. "
            "Поправьте переменную окружения APP_ENV.",
            file=sys.stderr,
        )
        raise SystemExit(1)


async def _seed_employee(session, *, role: str) -> dict:
    email = f"e2e-{role}-{RUN}@example.com"
    employee = Employee(
        full_name=f"E2E {role}", email=email, role=role, password_hash=hash_password(PASSWORD),
    )
    session.add(employee)
    await session.flush()
    return {"email": email, "password": PASSWORD}


async def _seed_discrepancy_application(session) -> dict:
    ruleset_row = await get_active_ruleset_version(session)
    if ruleset_row is None:
        raise RuntimeError("нет активной версии конфига правил — синхронизация ruleset ещё не прошла")

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
        client_id=client.id,
        external_id=f"E2E-{RUN}",
        status="to_operator",
        goods_name="Стиральная машина",
        goods_price=Decimal("50000.00"),
        down_payment=Decimal("5000.00"),
        term_months=12,
        tariff="standard",
        requested_amount=Decimal("45000.00"),
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
            from_outcome="manual_review", to_outcome="approve",
            reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
        )
    )
    # ФССП: живой ответ сайта — одно действующее и одно оконченное
    # производство (разворот подробностей в карточке).
    fixture = "/app/tests/fixtures/fssp_live_found.html"
    if os.path.exists(fixture):
        with open(fixture, encoding="utf-8") as handle:
            html = handle.read()
        now = datetime.now(timezone.utc)
        session.add(Check(
            application_id=application.id, subject_type="applicant", source="fssp", status="hit",
            request={"e2e": True}, response_raw={"html": html},
            parsed={"productions": [{"subject": "Задолженность", "amount": "3841.59"}]},
            started_at=now, finished_at=now, duration_ms=1,
        ))
        # Отчёт бюро — «Кредитный рейтинг» по образцу из описания блока
        # (Спектрум Дата): сводка по кредитам для разворота «Показать кредиты».
        session.add(BkiReport(
            client_id=application.client_id, application_id=application.id, status="ok", score=Decimal("736"),
            report_type="detailed", requested_at=now, completed_at=now,
            report_raw={"raw": {"data": [{"content": {"check_person": {"credit_history": {
                "bills": {"activeAccounts": {"borrowerCount": "3-4", "guarantorCount": "0-2"},
                          "activeCreditsSumLimit": "1000000.0-1999999.99", "averagePayment": "100000.0-149999.99"},
                "credits": {"hasBadDebtOrPassedToCollection": False,
                            "creditTypeInfosOpen": [{"type": 2, "typeDescription": "Потребительский кредит"}],
                            "creditTypeInfosClosed": [{"type": 1, "typeDescription": "Ипотека"}]},
                "activeCreditsDelays": {"maxCurrentDelay": "Оплата без просрочек",
                                        "maxHistoryDelay": "Просрочка от 30 до 59 дней", "sumDelay": "0.0-999.99"},
                "treatmentsInBanks": {"forLast7Days": 0, "forLast14Days": 0, "forLastMonth": 1, "all": 12},
                "score": {"value": "736"}, "src_meta": {"state": "OK"},
            }}}}]}},
        ))
        # Ещё блоки «Премиум»: розыск, суды, залоги.
        for source, block, status, summary in (
            ("wanted", "check_person.wanted", "ok", "в розыске МВД не числится"),
            ("courts", "check_person.general_jurisdiction_cases", "hit", "дел в судах: 2"),
            ("pledge", "check_person.pledge", "ok", "залогов: 1, имущество клиента в залоге: 1"),
        ):
            session.add(BkiBureauCheck(
                application_id=application.id, subject_type="applicant", source=source, block=block,
                status=status, report_type="detailed", parsed={"summary": summary},
            ))
        # ФССП из отчёта бюро — действующее и оконченное производство.
        session.add(BkiBureauCheck(
            application_id=application.id, subject_type="applicant", source="fssp",
            block="check_person.proceeding_v2.executive_v2", status="hit", report_type="detailed",
            parsed={
                "summary": "открытых производств: 1, долг 500.00 ₽", "open_count": 1, "closed_count": 1,
                "open_items": [{"ep_number": "01010/11/10102-ИП", "subject": "Страховые взносы, включая пени",
                                "debt_total": "500", "started": "2016-11-18", "office": "Тестовое РОСП"}],
                "closed_items": [{"ep_number": "01010/11/10101-ИП", "subject": "Налог на имущество",
                                  "debt_total": "0", "started": "2016-11-08", "ended": "2017-02-10",
                                  "office": "Тестовое РОСП"}],
            },
        ))
    await session.flush()
    return {
        "id": str(application.id), "external_id": application.external_id,
        "number": application.number, "last_name": last_name,
    }


async def _seed_frozen_sanctions_match(session) -> dict:
    """Заявка с санкционным хитом и активной заморозкой — для Playwright-теста
    на снятие заморозки при проверке по перечням (обязательная причина)."""
    ruleset_row = await get_active_ruleset_version(session)
    if ruleset_row is None:
        raise RuntimeError("нет активной версии конфига правил — синхронизация ruleset ещё не прошла")

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
        client_id=client.id,
        external_id=f"E2E-FREEZE-{RUN}",
        status="rejected_a",
        goods_name="Стиральная машина",
        goods_price=Decimal("50000.00"),
        down_payment=Decimal("5000.00"),
        term_months=12,
        tariff="standard",
        requested_amount=Decimal("45000.00"),
        profile={"last_name": last_name, "first_name": "Опасен", "middle_name": "Опасенович", "phone": "+79990000001"},
        consent_pd_at=now,
    )
    session.add(application)
    await session.flush()

    # T23: заморозка без самой проверки Росфинмониторинга — «Пометить ложным»
    # отвечал 409 «не найдена запись перечня». Как у конвейера: hit с ключом записи.
    session.add(Check(
        application_id=application.id, subject_type="applicant", source="rosfinmon", status="hit",
        request={"full_name": f"{last_name} Опасен Опасенович"},
        response_raw={"e2e": True},
        parsed={
            "sanctions_entry_key": f"e2e:{RUN}", "list_type": "te",
            "full_name": f"{last_name} Опасен Опасенович", "similarity": 1.0, "birth_date_matched": True,
        },
        started_at=now, finished_at=now, duration_ms=1,
    ))
    decision = Decision(
        application_id=application.id, stage="A", outcome="reject", reason_code="SANCTIONS_HIT",
        freeze=True, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=None,
    )
    session.add(decision)
    await session.flush()
    return {
        "application_id": str(application.id), "client_id": str(client.id),
        "decision_id": str(decision.id), "external_id": application.external_id,
    }


async def _seed_stage_b_decision(session, *, suffix: str, outcome: str, reason_code: str | None, features: dict) -> dict:
    """T20: решение стадии Б со снимком признаков — для теста блока «Почему»
    (причина / в норме / не вычислимо). Форма снимка — та же, что пишет
    stage_b_pipeline: score и declared_pti сверху, БКИ-признаки в features."""
    ruleset_row = await get_active_ruleset_version(session)
    now = datetime.now(timezone.utc)
    last_name = f"Причинов{suffix}{RUN}"
    client = Client(
        last_name=last_name, first_name="Тест", middle_name="Тестович",
        birth_date=date(1990, 1, 1), passport_series=f"{uuid.uuid4().int % 10000:04d}",
        passport_number=f"{uuid.uuid4().int % 1000000:06d}", phone="+79990000002",
    )
    session.add(client)
    await session.flush()
    application = Application(
        client_id=client.id, external_id=f"E2E-{suffix}-{RUN}",
        status="approved" if outcome == "approve" else "gray_zone",
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={"last_name": last_name, "first_name": "Тест", "middle_name": "Тестович", "phone": "+79990000002"},
        consent_pd_at=now,
    )
    session.add(application)
    await session.flush()
    snapshot = FeatureSnapshot(
        application_id=application.id, stage="B", features=features, feature_set_ver=STAGE_B_FEATURE_SET_VERSION,
    )
    session.add(snapshot)
    await session.flush()
    session.add(
        Decision(
            application_id=application.id, stage="B", outcome=outcome, reason_code=reason_code,
            freeze=False, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=snapshot.id,
            score=Decimal(str(features["score"])), score_source="bki_score",
        )
    )
    await session.flush()
    return {"id": str(application.id)}


async def _add_guarantor_with_history(session, application_id: str) -> dict:
    """Поручитель для карточки серой зоны: прежний (анкета до повторного
    «Рассмотреть») и текущий. По прежнему ФССП «ничего не найдено», по
    текущему — сайт был недоступен: проверка ФССП поручителя не пройдена.
    В ленте и на шкале должен учитываться только текущий."""
    now = datetime.now(timezone.utc)
    # created_at прежнего — раньше: в одной транзакции now() у обоих одинаков,
    # и «текущий» (последний записанный) выбирался бы случайно.
    old = Guarantor(application_id=application_id, profile={"last_name": "Прежний", "first_name": "Поручитель"},
                    status="used", created_at=now - timedelta(hours=1))
    current = Guarantor(
        application_id=application_id, status="used",
        profile={"last_name": f"Поручителев{RUN}", "first_name": "Пётр", "middle_name": "Петрович",
                 "birth_date": "1980-02-03"},
    )
    session.add_all([old, current])
    await session.flush()
    for guarantor, status in ((old, "ok"), (current, "source_unavailable")):
        session.add(Check(
            application_id=application_id, subject_type="guarantor", subject_id=guarantor.id, source="fssp",
            status=status, request={"e2e": True}, response_raw={"e2e": True}, parsed=None,
            started_at=now, finished_at=now, duration_ms=1,
        ))
    await session.flush()
    return {"last_name": f"Поручителев{RUN}"}


async def main() -> None:
    _refuse_if_production()

    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            # T23: ролей три — admin, operator, viewer.
            viewer = await _seed_employee(session, role="viewer")
            admin = await _seed_employee(session, role="admin")
            operator = await _seed_employee(session, role="operator")
            application = await _seed_discrepancy_application(session)
            frozen_match = await _seed_frozen_sanctions_match(session)
            # Балл выше порога одобрения (700 в dev.yaml) и ПДН в норме, но ФССП
            # не проверено — пример из задания T20.
            gray_fssp = await _seed_stage_b_decision(
                session, suffix="GRAY", outcome="manual_review", reason_code="FSSP_NOT_CHECKED",
                # T22: платёж по рассрочке рассчитан по тарифу — строка «Платёж
                # в месяц» в «Сделке» с пометкой источника.
                features={
                    "score": 760, "declared_pti": 0.1, "installment_monthly_payment": 4500.0,
                    "installment_payment_source": "tariff",
                    "features": {"pti": 0.42, "bki_active_contracts_count": 1},
                },
            )
            gray_fssp["guarantor"] = await _add_guarantor_with_history(session, gray_fssp["id"])
            # Доход нулевой: declared_pti и pti — None, КИ получена.
            no_income = await _seed_stage_b_decision(
                # Балл между порогами — серая зона без ФССП-причины: «Нужно
                # сделать: решение человека» (T22).
                session, suffix="NOINCOME", outcome="manual_review", reason_code="SCORE_GRAY_ZONE",
                features={"score": 600, "declared_pti": None, "features": {"pti": None, "bki_active_contracts_count": 0}},
            )
            await session.commit()
    finally:
        await engine.dispose()

    print(json.dumps({
        "viewer": viewer, "admin": admin,
        "application": application, "frozen_match": frozen_match, "operator": operator,
        "gray_fssp": gray_fssp, "no_income": no_income,
    }))


if __name__ == "__main__":
    asyncio.run(main())
