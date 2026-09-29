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
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.config import get_settings
from app.db import new_sessionmaker
from app.engine.versions import get_active_ruleset_version
from app.models import Application, Client, Decision, Employee, ManualOverride
from app.security.passwords import hash_password

RUN = uuid.uuid4().hex[:8]
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
    await session.flush()
    return {
        "id": str(application.id), "external_id": application.external_id,
        "number": application.number, "last_name": last_name,
    }


async def _seed_frozen_sanctions_match(session) -> dict:
    """Заявка с санкционным хитом и активной заморозкой — для Playwright-теста
    на снятие заморозки комплаенсом (обязательная причина)."""
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

    decision = Decision(
        application_id=application.id, stage="A", outcome="reject", reason_code="SANCTIONS_HIT",
        freeze=True, triggered_rules=[], ruleset_version_id=ruleset_row.id, feature_snapshot_id=None,
    )
    session.add(decision)
    await session.flush()
    return {"client_id": str(client.id), "decision_id": str(decision.id), "external_id": application.external_id}


async def main() -> None:
    _refuse_if_production()

    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            risk_manager = await _seed_employee(session, role="risk_manager")
            viewer = await _seed_employee(session, role="viewer")
            compliance = await _seed_employee(session, role="compliance")
            application = await _seed_discrepancy_application(session)
            frozen_match = await _seed_frozen_sanctions_match(session)
            await session.commit()
    finally:
        await engine.dispose()

    print(json.dumps({
        "risk_manager": risk_manager, "viewer": viewer, "compliance": compliance,
        "application": application, "frozen_match": frozen_match,
    }))


if __name__ == "__main__":
    asyncio.run(main())
