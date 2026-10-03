"""T13c: дашборд — агрегаты считает бэкенд, цифры должны сходиться точно
(ваше явное указание: «дашборд с неверными цифрами хуже, чем без дашборда»).

Период и группировка по неделям/месяцам — по московскому времени (ваше
уточнение): 2026-01-04 22:30 UTC — это уже 2026-01-05 01:30 по Москве
(понедельник), хотя в UTC это ещё воскресенье прошлой недели. Один из
сценариев ниже намеренно использует такую «ночную» метку, чтобы проверить,
что группировка идёт по Москве, а не по UTC."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models import Application, BkiReport, Check, Client, Decision, ManualOverride
from tests._admin_helpers import create_employee, login
from tests.test_engine_pipeline import active_ruleset  # noqa: F401

_PERIOD_FROM = "2026-01-01"
_PERIOD_TO = "2026-02-01"


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


async def _application(session, *, external_id: str, status: str, submitted_at: datetime) -> Application:
    application = Application(
        external_id=external_id, status=status,
        goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
        term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
        profile={}, consent_pd_at=submitted_at, submitted_at=submitted_at,
    )
    session.add(application)
    await session.flush()
    return application


async def _decision(
    session, *, application_id, stage: str, outcome: str, reason_code: str | None,
    created_at: datetime, ruleset_version_id,
) -> Decision:
    decision = Decision(
        application_id=application_id, stage=stage, outcome=outcome, reason_code=reason_code,
        freeze=False, triggered_rules=[], ruleset_version_id=ruleset_version_id, created_at=created_at,
    )
    session.add(decision)
    await session.flush()
    return decision


async def test_dashboard_summary_matches_exact_seeded_numbers(
    admin_client, session_factory, active_ruleset
):
    async with session_factory() as session:
        # App1: одобрена, решение стадии Б approve в ночь на понедельник по
        # Москве (2026-01-04 22:30 UTC = 2026-01-05 01:30 MSK) — неделя 1,
        # хотя в UTC это воскресенье прошлой недели. Есть override, СОВПАДАЮЩИЙ
        # с решением — учитывается в overridden, но не в discrepancies.
        app1 = await _application(
            session, external_id=f"DASH-1-{uuid.uuid4().hex[:6]}", status="approved",
            submitted_at=_utc(2026, 1, 4, 12, 0),
        )
        await _decision(
            session, application_id=app1.id, stage="B", outcome="approve", reason_code=None,
            created_at=_utc(2026, 1, 4, 22, 30), ruleset_version_id=active_ruleset.id,
        )
        session.add(ManualOverride(
            application_id=app1.id, from_outcome="approve", to_outcome="approve",
            reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
        ))

        # App2: отказ стадии Б, причина PTI_HIGH, в тот же понедельник (неделя 1).
        app2 = await _application(
            session, external_id=f"DASH-2-{uuid.uuid4().hex[:6]}", status="rejected_b",
            submitted_at=_utc(2026, 1, 5, 9, 0),
        )
        await _decision(
            session, application_id=app2.id, stage="B", outcome="reject", reason_code="PTI_HIGH",
            created_at=_utc(2026, 1, 5, 10, 0), ruleset_version_id=active_ruleset.id,
        )

        # App3: одобрена, решение стадии Б approve на следующей неделе (неделя 2).
        app3 = await _application(
            session, external_id=f"DASH-3-{uuid.uuid4().hex[:6]}", status="approved",
            submitted_at=_utc(2026, 1, 12, 7, 0),
        )
        await _decision(
            session, application_id=app3.id, stage="B", outcome="approve", reason_code=None,
            created_at=_utc(2026, 1, 12, 8, 0), ruleset_version_id=active_ruleset.id,
        )

        # App4: отказ стадии А, причина AGE_BELOW_MIN — считается в причинах
        # отказов, но НЕ в доле одобрений по неделям (та смотрит только стадию Б).
        app4 = await _application(
            session, external_id=f"DASH-4-{uuid.uuid4().hex[:6]}", status="rejected_a",
            submitted_at=_utc(2026, 1, 6, 9, 0),
        )
        await _decision(
            session, application_id=app4.id, stage="A", outcome="reject", reason_code="AGE_BELOW_MIN",
            created_at=_utc(2026, 1, 6, 10, 0), ruleset_version_id=active_ruleset.id,
        )

        # App5: у оператора, решения ещё нет.
        await _application(
            session, external_id=f"DASH-5-{uuid.uuid4().hex[:6]}", status="to_operator",
            submitted_at=_utc(2026, 1, 7, 9, 0),
        )

        # App6: серая зона, решение стадии Б manual_review (неделя 1), но
        # override — approve: РАСХОЖДЕНИЕ с оператором.
        app6 = await _application(
            session, external_id=f"DASH-6-{uuid.uuid4().hex[:6]}", status="gray_zone",
            submitted_at=_utc(2026, 1, 7, 8, 0),
        )
        await _decision(
            session, application_id=app6.id, stage="B", outcome="manual_review", reason_code="SCORE_GRAY_ZONE",
            created_at=_utc(2026, 1, 7, 9, 0), ruleset_version_id=active_ruleset.id,
        )
        session.add(ManualOverride(
            application_id=app6.id, from_outcome="manual_review", to_outcome="approve",
            reason_code="PARTNER_OPERATOR_OVERRIDE_APPROVE",
        ))

        # Контроль: заявка ДО периода — не должна попасть ни в один блок.
        await _application(
            session, external_id=f"DASH-OLD-{uuid.uuid4().hex[:6]}", status="approved",
            submitted_at=_utc(2025, 6, 1, 12, 0),
        )

        # Проверки — доля source_unavailable по источнику (на App1).
        for src, status in (("mvd", "ok"), ("mvd", "ok"), ("mvd", "source_unavailable"),
                            ("fns", "source_unavailable"), ("fns", "source_unavailable"),
                            ("rosfinmon", "ok")):
            session.add(Check(
                application_id=app1.id, subject_type="applicant", source=src, status=status,
                started_at=_utc(2026, 1, 10, 12, 0),
            ))

        # БКИ — платные попытки за месяц, успешные отдельно.
        client = Client(
            last_name="Т", first_name="Т", birth_date=date(1990, 1, 1),
            passport_series=f"{uuid.uuid4().int % 10000:04d}", passport_number=f"{uuid.uuid4().int % 1000000:06d}",
            phone="+70000000000",
        )
        session.add(client)
        await session.flush()
        for status in ("ok", "ok", "no_data"):
            session.add(BkiReport(
                client_id=client.id, application_id=app1.id, status=status,
                requested_at=_utc(2026, 1, 15, 12, 0),
            ))

        await session.commit()

    await create_employee(session_factory, email="dash-rm@example.com", role="risk_manager")
    await login(admin_client, "dash-rm@example.com")

    response = await admin_client.get(
        "/api/dashboard/summary", params={"period_from": _PERIOD_FROM, "period_to": _PERIOD_TO}
    )
    assert response.status_code == 200
    body = response.json()

    # --- воронка по состояниям -------------------------------------------------
    funnel = {item["status"]: item["count"] for item in body["funnel"]}
    assert funnel == {
        "approved": 2, "rejected_b": 1, "rejected_a": 1, "to_operator": 1, "gray_zone": 1,
    }

    # --- доля одобрений по неделям (по Москве, не UTC) --------------------------
    weekly = {item["week_start"]: (item["approved"], item["total"]) for item in body["weekly_approval"]}
    assert weekly == {
        "2026-01-05": (1, 3),  # App1 (approve), App2 (reject), App6 (manual_review)
        "2026-01-12": (1, 1),  # App3 (approve)
    }

    # --- причины отказов ---------------------------------------------------------
    reasons = {item["reason_code"]: item["count"] for item in body["rejection_reasons"]}
    assert reasons == {"PTI_HIGH": 1, "AGE_BELOW_MIN": 1}

    # --- переопределения и расхождения -------------------------------------------
    assert body["overrides"] == {"applications_total": 6, "overridden": 2, "discrepancies": 1}

    # --- БКИ по месяцам ------------------------------------------------------------
    assert body["bki_monthly"] == [{"month": "2026-01", "attempts": 3, "successful": 2}]

    # --- доля source_unavailable по источнику --------------------------------------
    availability = {item["source"]: (item["unavailable"], item["total"]) for item in body["source_availability"]}
    assert availability == {"mvd": (1, 3), "fns": (2, 2), "rosfinmon": (0, 1)}


async def test_dashboard_forbidden_for_admin_and_compliance(admin_client, session_factory):
    await create_employee(session_factory, email="dash-admin@example.com", role="admin")
    await login(admin_client, "dash-admin@example.com")
    response = await admin_client.get("/api/dashboard/summary")
    assert response.status_code == 403


async def test_dashboard_empty_period_returns_empty_blocks(admin_client, session_factory):
    await create_employee(session_factory, email="dash-rm2@example.com", role="risk_manager")
    await login(admin_client, "dash-rm2@example.com")

    response = await admin_client.get(
        "/api/dashboard/summary", params={"period_from": "1999-01-01", "period_to": "1999-02-01"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["funnel"] == []
    assert body["weekly_approval"] == []
    assert body["rejection_reasons"] == []
    assert body["overrides"] == {"applications_total": 0, "overridden": 0, "discrepancies": 0}
    assert body["bki_monthly"] == []
    assert body["source_availability"] == []


async def test_dashboard_vintage_and_delinquency_exact_numbers(admin_client, session_factory):
    """T15: винтаж и просрочка — реальные числа, не заглушка T13c."""
    from app.engine.delinquency_recompute import recompute_all_contracts
    from app.models import Contract, ImportBatch, Payment, PaymentSchedule

    employee_id = await create_employee(session_factory, email="dash-rm3@example.com", role="risk_manager")
    await login(admin_client, "dash-rm3@example.com")

    async with session_factory() as session:
        # Когорта 2025-01: один договор вовремя погашен (bad=0), один — без единого платежа (bad=1).
        good = Contract(
            application_id=None, external_number=f"DASH-GOOD-{uuid.uuid4().hex[:6]}", principal=Decimal("6000"),
            markup=Decimal("0"), total_amount=Decimal("6000"), down_payment=Decimal("0"), term_months=6,
            issued_at=date(2025, 1, 1), status="active",
        )
        bad = Contract(
            application_id=None, external_number=f"DASH-BAD-{uuid.uuid4().hex[:6]}", principal=Decimal("6000"),
            markup=Decimal("0"), total_amount=Decimal("6000"), down_payment=Decimal("0"), term_months=6,
            issued_at=date(2025, 1, 1), status="active",
        )
        session.add_all([good, bad])
        await session.flush()
        for n in range(1, 7):
            month = 1 + n
            year = 2025 + (month - 1) // 12
            month = (month - 1) % 12 + 1
            due = date(year, month, 1)
            session.add(PaymentSchedule(contract_id=good.id, period_no=n, due_date=due, due_amount=Decimal("1000")))
            session.add(Payment(contract_id=good.id, paid_at=due, amount=Decimal("1000"), source="import", kind="regular"))
            session.add(PaymentSchedule(contract_id=bad.id, period_no=n, due_date=due, due_amount=Decimal("1000")))

        session.add(
            ImportBatch(
                kind="payments", filename="x.csv", file_sha256=uuid.uuid4().hex.ljust(64, "0"), storage_key="x",
                uploaded_by=employee_id, status="completed", data_as_of=date(2026, 1, 1),
            )
        )
        await session.commit()

    async with session_factory() as session:
        await recompute_all_contracts(session, upto_date=date(2026, 1, 1))
        await session.commit()

    response = await admin_client.get("/api/dashboard/summary")
    assert response.status_code == 200
    body = response.json()

    cohort = next(v for v in body["vintage"] if v["cohort_month"] == "2025-01")
    assert cohort["total_contracts"] == 2
    assert cohort["mature_contracts"] == 2
    assert cohort["bad_contracts"] == 1
    assert cohort["bad_rate"] == 0.5

    buckets = {b["bucket"]: b["contracts"] for b in body["delinquency_buckets"]}
    assert buckets.get("90+", 0) >= 1  # bad-контракт по состоянию на 2026-01-01 глубоко просрочен
    assert buckets.get("0", 0) >= 1  # good-контракт полностью погашен, без просрочки
