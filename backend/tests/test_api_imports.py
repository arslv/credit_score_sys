"""T15: импорт договоров/платежей/исторических заявок."""

import csv
import io
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.models import (
    AuditLog,
    Contract,
    ContractLinkCandidate,
    DelinquencyDaily,
    ImportBatch,
    Payment,
    PaymentSchedule,
)
from tests._admin_helpers import create_employee, login


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


_CONTRACTS_HEADER = [
    "Номер договора", "Сумма товара", "Наценка", "Итого к оплате", "Первый взнос",
    "Срок мес", "Дата выдачи", "Статус", "Номер заявки", "ФИО", "Телефон",
]


async def _seed_application(session_factory, *, external_id: str) -> uuid.UUID:
    from app.models import Application

    async with session_factory() as session:
        application = Application(
            external_id=external_id, status="approved",
            goods_name="Стиральная машина", goods_price=Decimal("50000.00"), down_payment=Decimal("5000.00"),
            term_months=12, tariff="standard", requested_amount=Decimal("45000.00"),
            profile={}, consent_pd_at=datetime.now(timezone.utc), submitted_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.commit()
        return application.id


async def test_contract_import_matches_by_exact_external_id(admin_client, session_factory):
    await create_employee(session_factory, email="rm1@example.com", role="admin")
    await login(admin_client, "rm1@example.com")

    external_id = f"APP-{uuid.uuid4().hex[:8]}"
    await _seed_application(session_factory, external_id=external_id)

    number = f"K-{uuid.uuid4().hex[:8]}"
    body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "50000", "5000", "55000", "5000", "12", "01.01.2026", "Действует", external_id, "Иванов Иван", "+79990000000"]],
    )

    response = await admin_client.post(
        "/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["created"] is True
    assert result["inserted"] == 1
    assert result["unmatched_contracts"] == []

    async with session_factory() as session:
        contract = (await session.execute(select(Contract).where(Contract.external_number == number))).scalar_one()
        assert contract.status == "active"
        assert contract.application_id is not None

        schedule = (
            await session.execute(select(PaymentSchedule).where(PaymentSchedule.contract_id == contract.id))
        ).scalars().all()
        assert len(schedule) == 12
        assert sum(s.due_amount for s in schedule) == Decimal("50000.00")  # total - down_payment

        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "import_data"))
        ).scalars().all()
        assert len(audit_rows) == 1


async def test_contract_import_unmatched_creates_candidate_not_auto_linked(admin_client, session_factory):
    """Ваше явное указание: автоматически — только по однозначному
    идентификатору. Телефон/ФИО — только кандидат, контракт остаётся
    непривязанным."""
    await create_employee(session_factory, email="rm2@example.com", role="admin")
    await login(admin_client, "rm2@example.com")

    from app.engine.clients import find_or_create_client
    from tests.conftest import unique_valid_passport_series

    phone = "+79995551234"
    async with session_factory() as session:
        client, _ = await find_or_create_client(
            session, last_name="Петров", first_name="Пётр", middle_name=None,
            birth_date=date(1990, 1, 1), passport_series=unique_valid_passport_series(),
            passport_number=f"{uuid.uuid4().int % 1000000:06d}", phone=phone,
        )
        from app.models import Application

        application = Application(
            external_id=f"APP-{uuid.uuid4().hex[:8]}", status="approved", client_id=client.id,
            goods_name="Товар", goods_price=Decimal("10000.00"), down_payment=Decimal("0"),
            term_months=6, tariff="standard", requested_amount=Decimal("10000.00"),
            profile={}, consent_pd_at=datetime.now(timezone.utc), submitted_at=datetime.now(timezone.utc),
        )
        session.add(application)
        await session.commit()
        application_id = application.id

    number = f"K-{uuid.uuid4().hex[:8]}"
    body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "Петров Пётр", phone]],
    )
    response = await admin_client.post(
        "/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["unmatched_contracts"]) == 1
    assert result["candidates_created"] == 1

    async with session_factory() as session:
        contract = (await session.execute(select(Contract).where(Contract.external_number == number))).scalar_one()
        assert contract.application_id is None  # НЕ привязан автоматически

        candidate = (
            await session.execute(
                select(ContractLinkCandidate).where(ContractLinkCandidate.contract_id == contract.id)
            )
        ).scalar_one()
        assert candidate.application_id == application_id
        assert candidate.status == "pending"


async def test_confirm_contract_candidate_links_and_audits(admin_client, session_factory):
    await create_employee(session_factory, email="rm3@example.com", role="admin")
    await login(admin_client, "rm3@example.com")

    application_id = await _seed_application(session_factory, external_id=f"APP-{uuid.uuid4().hex[:8]}")

    async with session_factory() as session:
        contract = Contract(
            application_id=None, external_number=f"K-{uuid.uuid4().hex[:8]}",
            principal=Decimal("10000"), markup=Decimal("0"), total_amount=Decimal("10000"),
            down_payment=Decimal("0"), term_months=6, issued_at=date(2026, 1, 1), status="active",
        )
        session.add(contract)
        await session.flush()
        candidate = ContractLinkCandidate(
            contract_id=contract.id, application_id=application_id, match_reason="телефон",
            source_name="Тест Тестов", source_phone="+79990000000",
        )
        session.add(candidate)
        await session.commit()
        contract_id, candidate_id = contract.id, candidate.id

    response = await admin_client.post(f"/api/imports/contract-candidates/{candidate_id}/confirm")
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        contract = await session.get(Contract, contract_id)
        assert contract.application_id == application_id

        candidate = await session.get(ContractLinkCandidate, candidate_id)
        assert candidate.status == "confirmed"
        assert candidate.resolved_by is not None

        audit_rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == "link_contract"))
        ).scalars().all()
        assert len(audit_rows) == 1


async def test_reimporting_same_contracts_file_does_not_duplicate(admin_client, session_factory):
    await create_employee(session_factory, email="rm4@example.com", role="admin")
    await login(admin_client, "rm4@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""]],
    )

    first = await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")})
    second = await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["batch_id"] == second.json()["batch_id"]

    async with session_factory() as session:
        count = (
            await session.execute(select(func.count()).select_from(Contract).where(Contract.external_number == number))
        ).scalar_one()
        assert count == 1

        batches = (
            await session.execute(select(func.count()).select_from(ImportBatch))
        ).scalar_one()
        assert batches == 1


async def test_payments_import_separates_penalty_and_applies_reversal(admin_client, session_factory):
    await create_employee(session_factory, email="rm5@example.com", role="admin")
    await login(admin_client, "rm5@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "12000", "0", "12000", "0", "12", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", contracts_body, "text/csv")})

    payments_header = ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"]
    payments_body = _csv_bytes(
        payments_header,
        [
            [number, "01.02.2026", "1000", "Платёж по графику"],
            [number, "05.02.2026", "200", "Неустойка"],
            [number, "10.02.2026", "1000", "Сторно"],
        ],
    )
    response = await admin_client.post(
        "/api/imports/payments",
        data={"data_as_of": "2026-02-15"},
        files={"file": ("payments.csv", payments_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inserted"] == 3

    async with session_factory() as session:
        contract = (await session.execute(select(Contract).where(Contract.external_number == number))).scalar_one()
        payments = (
            await session.execute(select(Payment).where(Payment.contract_id == contract.id).order_by(Payment.paid_at))
        ).scalars().all()
        assert [p.kind for p in payments] == ["regular", "penalty", "reversal"]
        assert all(p.amount >= 0 for p in payments)  # знак у кода (kind), не у суммы

        batch = (
            await session.execute(select(ImportBatch).where(ImportBatch.kind == "payments"))
        ).scalar_one()
        assert batch.data_as_of == date(2026, 2, 15)


async def test_historical_application_import_has_no_feature_snapshots(admin_client, session_factory):
    await create_employee(session_factory, email="rm6@example.com", role="admin")
    await login(admin_client, "rm6@example.com")

    header = [
        "Номер заявки", "Фамилия", "Имя", "Отчество", "Телефон", "Товар", "Сумма",
        "Срок мес", "Тариф", "Дата заявки", "Результат",
    ]
    body = _csv_bytes(
        header,
        [["OLD-1", "Сидоров", "Сидор", "", "+79990000001", "Диван", "30000", "12", "standard", "01.01.2025", "Отказ"]],
    )
    response = await admin_client.post(
        "/api/imports/historical-applications", files={"file": ("hist.csv", body, "text/csv")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["inserted"] == 1

    async with session_factory() as session:
        from app.models import Application, Decision, FeatureSnapshot

        application = (
            await session.execute(select(Application).where(Application.external_id.like("HIST-%")))
        ).scalar_one()
        assert application.source == "historical_import"
        assert application.status == "rejected_a"

        decisions = (
            await session.execute(select(Decision).where(Decision.application_id == application.id))
        ).scalars().all()
        snapshots = (
            await session.execute(select(FeatureSnapshot).where(FeatureSnapshot.application_id == application.id))
        ).scalars().all()
        assert decisions == []
        assert snapshots == []


async def test_admin_can_upload_imports(admin_client, session_factory):
    """Ваше явное указание: роли admin и risk_manager (не только risk_manager,
    в отличие от T14 — это другой экран)."""
    await create_employee(session_factory, email="admin1@example.com", role="admin")
    await login(admin_client, "admin1@example.com")

    body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[f"K-{uuid.uuid4().hex[:8]}", "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""]],
    )
    response = await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")})
    assert response.status_code == 200, response.text


async def test_viewer_forbidden_from_uploading(admin_client, session_factory):
    await create_employee(session_factory, email="viewer1@example.com", role="viewer")
    await login(admin_client, "viewer1@example.com")

    body = _csv_bytes(_CONTRACTS_HEADER, [])
    response = await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")})
    assert response.status_code == 403


async def test_contract_import_classifies_closure_outcome_from_config(admin_client, session_factory):
    """Ваше замечание после первой версии: закрытие договора — не всегда
    погашение. Трактовка причины закрытия — целиком из
    config/import_mappings/contracts.yaml::closure_outcome_map, тест на все
    три ветки: известный хороший исход, известный плохой исход, нераспознанная
    причина -> unknown (а не 0/1)."""
    await create_employee(session_factory, email="rm-closure@example.com", role="admin")
    await login(admin_client, "rm-closure@example.com")

    header = _CONTRACTS_HEADER + ["Дата закрытия", "Причина закрытия"]
    good_number = f"K-{uuid.uuid4().hex[:8]}"
    bad_number = f"K-{uuid.uuid4().hex[:8]}"
    unknown_number = f"K-{uuid.uuid4().hex[:8]}"
    body = _csv_bytes(
        header,
        [
            [good_number, "10000", "0", "10000", "0", "6", "01.01.2026", "Закрыт", "", "", "", "01.06.2026", "Погашен досрочно"],
            [bad_number, "10000", "0", "10000", "0", "6", "01.01.2026", "Изъятие", "", "", "", "01.06.2026", "Товар изъят"],
            [unknown_number, "10000", "0", "10000", "0", "6", "01.01.2026", "Закрыт", "", "", "", "01.06.2026", "Расторгнут по суду"],
        ],
    )
    response = await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", body, "text/csv")})
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        good = (await session.execute(select(Contract).where(Contract.external_number == good_number))).scalar_one()
        bad = (await session.execute(select(Contract).where(Contract.external_number == bad_number))).scalar_one()
        unknown = (
            await session.execute(select(Contract).where(Contract.external_number == unknown_number))
        ).scalar_one()
        assert good.closure_outcome == "good"
        assert bad.closure_outcome == "bad"
        assert unknown.closure_outcome == "unknown"
        assert unknown.closure_reason == "Расторгнут по суду"


async def test_payments_import_warns_when_contract_missing_from_contracts_export_same_date(
    admin_client, session_factory,
):
    """Ваше замечание после первой версии: предупреждение нельзя строить на
    наличии строк в файле ПЛАТЕЖЕЙ (просрочник, который не платит, там
    закономерно отсутствует). Сверяем с выгрузкой ДОГОВОРОВ на ту же дату:
    если действующий договор выпал из неё — это и есть признак неполноты."""
    await create_employee(session_factory, email="rm-missing@example.com", role="admin")
    await login(admin_client, "rm-missing@example.com")

    stays = f"K-{uuid.uuid4().hex[:8]}"
    drops_out = f"K-{uuid.uuid4().hex[:8]}"

    # Первая выгрузка договоров (без даты) заводит оба договора как действующие.
    initial_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [
            [stays, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""],
            [drops_out, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""],
        ],
    )
    await admin_client.post("/api/imports/contracts", files={"file": ("initial.csv", initial_body, "text/csv")})

    # Вторая выгрузка договоров, датированная 2026-02-15 (та же дата, что и
    # платежи ниже), содержит только один из двух — второй из неё выпал.
    dated_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[stays, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post(
        "/api/imports/contracts", data={"data_as_of": "2026-02-15"},
        files={"file": ("dated.csv", dated_body, "text/csv")},
    )

    payments_header = ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"]
    payments_body = _csv_bytes(payments_header, [[stays, "01.02.2026", "1000", "Платёж по графику"]])
    response = await admin_client.post(
        "/api/imports/payments", data={"data_as_of": "2026-02-15"},
        files={"file": ("payments.csv", payments_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["missing_active_contracts"] == [drops_out]


async def test_payments_import_no_warning_when_contracts_export_same_date_has_contract(
    admin_client, session_factory,
):
    await create_employee(session_factory, email="rm-complete@example.com", role="admin")
    await login(admin_client, "rm-complete@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post(
        "/api/imports/contracts", data={"data_as_of": "2026-02-15"},
        files={"file": ("contracts.csv", contracts_body, "text/csv")},
    )

    payments_header = ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"]
    payments_body = _csv_bytes(payments_header, [[number, "01.02.2026", "1000", "Платёж по графику"]])
    response = await admin_client.post(
        "/api/imports/payments", data={"data_as_of": "2026-02-15"},
        files={"file": ("payments.csv", payments_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["missing_active_contracts"] == []


async def test_payments_import_no_warning_for_nonpaying_contract_present_in_contracts_export(
    admin_client, session_factory,
):
    """Ваш явный тест: договор без единого платежа в файле платежей, но
    присутствующий в файле договоров (на ту же дату), предупреждения не
    вызывает — это настоящий просрочник, не дыра в выгрузке."""
    await create_employee(session_factory, email="rm-nonpayer@example.com", role="admin")
    await login(admin_client, "rm-nonpayer@example.com")

    payer = f"K-{uuid.uuid4().hex[:8]}"
    nonpayer = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [
            [payer, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""],
            [nonpayer, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""],
        ],
    )
    await admin_client.post(
        "/api/imports/contracts", data={"data_as_of": "2026-02-15"},
        files={"file": ("contracts.csv", contracts_body, "text/csv")},
    )

    # nonpayer — ни одной строки в файле платежей (ни единого платежа), но ОН ЕСТЬ в выгрузке договоров.
    payments_header = ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"]
    payments_body = _csv_bytes(payments_header, [[payer, "01.02.2026", "1000", "Платёж по графику"]])
    response = await admin_client.post(
        "/api/imports/payments", data={"data_as_of": "2026-02-15"},
        files={"file": ("payments.csv", payments_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["missing_active_contracts"] == []


async def test_payments_import_no_check_when_no_contracts_export_for_same_date(admin_client, session_factory):
    """Нет выгрузки договоров с такой же датой (например, договоры загружены
    один раз без даты) — сверить не с чем, предупреждение не выдаётся вовсе
    (не угадывается по файлу платежей, docs/payments_import.md)."""
    await create_employee(session_factory, email="rm-nodate@example.com", role="admin")
    await login(admin_client, "rm-nodate@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", contracts_body, "text/csv")})

    payments_header = ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"]
    payments_body = _csv_bytes(payments_header, [])
    response = await admin_client.post(
        "/api/imports/payments", data={"data_as_of": "2026-02-15"},
        files={"file": ("payments.csv", payments_body, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["missing_active_contracts"] == []


async def test_reimporting_contracts_with_known_external_number_updates_in_place(admin_client, session_factory):
    """Ваше замечание подразумевает периодическую выгрузку договоров —
    повторная загрузка с уже известным external_number обновляет
    существующий договор (например, стал закрыт), а не создаёт дубликат и не
    падает на уникальности external_number."""
    await create_employee(session_factory, email="rm-reimport@example.com", role="admin")
    await login(admin_client, "rm-reimport@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    first_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""]],
    )
    first = await admin_client.post(
        "/api/imports/contracts", data={"data_as_of": "2026-01-15"},
        files={"file": ("first.csv", first_body, "text/csv")},
    )
    assert first.json()["inserted"] == 1
    assert first.json()["updated"] == 0

    header_with_closure = _CONTRACTS_HEADER + ["Дата закрытия", "Причина закрытия"]
    second_body = _csv_bytes(
        header_with_closure,
        [[number, "10000", "0", "10000", "0", "6", "01.01.2026", "Закрыт", "", "", "", "01.03.2026", "Погашен досрочно"]],
    )
    second = await admin_client.post(
        "/api/imports/contracts", data={"data_as_of": "2026-03-15"},
        files={"file": ("second.csv", second_body, "text/csv")},
    )
    assert second.json()["inserted"] == 0
    assert second.json()["updated"] == 1

    async with session_factory() as session:
        rows = (
            await session.execute(select(Contract).where(Contract.external_number == number))
        ).scalars().all()
        assert len(rows) == 1  # не дубликат
        assert rows[0].status == "closed"
        assert rows[0].closure_outcome == "good"


_SCHEDULE_HEADER = ["Номер договора", "№ периода", "Дата платежа", "Сумма"]


async def test_payment_schedule_import_replaces_generated_schedule(admin_client, session_factory):
    """Ваше замечание: график — отдельным импортом, со своим маппингом;
    генерация равными платежами — только запасной вариант. Если график
    пришёл, он заменяет сгенерированный и ставит schedule_source=imported."""
    await create_employee(session_factory, email="rm-schedule@example.com", role="admin")
    await login(admin_client, "rm-schedule@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "12000", "0", "12000", "0", "3", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", contracts_body, "text/csv")})

    async with session_factory() as session:
        contract = (await session.execute(select(Contract).where(Contract.external_number == number))).scalar_one()
        assert contract.schedule_source == "generated"
        generated = (
            await session.execute(select(PaymentSchedule).where(PaymentSchedule.contract_id == contract.id))
        ).scalars().all()
        assert len(generated) == 3
        assert {s.due_amount for s in generated} == {Decimal("4000.00")}  # равными платежами

    # Неравный график, пришедший из выгрузки, не совпадает с равными платежами.
    schedule_body = _csv_bytes(
        _SCHEDULE_HEADER,
        [
            [number, "1", "01.02.2026", "2000"],
            [number, "2", "01.03.2026", "5000"],
            [number, "3", "01.04.2026", "5000"],
        ],
    )
    response = await admin_client.post(
        "/api/imports/payment-schedule", files={"file": ("schedule.csv", schedule_body, "text/csv")}
    )
    assert response.status_code == 200, response.text
    assert response.json()["inserted"] == 3

    async with session_factory() as session:
        contract = (await session.execute(select(Contract).where(Contract.external_number == number))).scalar_one()
        assert contract.schedule_source == "imported"
        imported = (
            await session.execute(
                select(PaymentSchedule).where(PaymentSchedule.contract_id == contract.id).order_by(PaymentSchedule.period_no)
            )
        ).scalars().all()
        assert len(imported) == 3  # старый сгенерированный график заменён, не добавлен
        assert [s.due_amount for s in imported] == [Decimal("2000.00"), Decimal("5000.00"), Decimal("5000.00")]

        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "import_data")
            )
        ).scalars().all()
        assert any(a.details.get("kind") == "payment_schedule" for a in audit_rows)


async def test_imported_schedule_produces_different_delinquency_than_generated(admin_client, session_factory):
    """Ваше явное указание: один и тот же договор с импортированным неравным
    графиком и с генерированным даёт разную просрочку, и используется
    импортированный."""
    from app.engine.delinquency_recompute import load_grace_days, recompute_contract
    from app.models import DelinquencyDaily

    await create_employee(session_factory, email="rm-dpd@example.com", role="admin")
    await login(admin_client, "rm-dpd@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "12000", "0", "12000", "0", "3", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", contracts_body, "text/csv")})

    # Единственный платёж — 2000, внесён 01.02.2026 (ровно в срок первого периода).
    payments_header = ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"]
    payments_body = _csv_bytes(payments_header, [[number, "01.02.2026", "2000", "Платёж по графику"]])
    await admin_client.post(
        "/api/imports/payments", data={"data_as_of": "2026-04-15"},
        files={"file": ("payments.csv", payments_body, "text/csv")},
    )

    upto_date = date(2026, 4, 15)
    grace_days = load_grace_days()

    async with session_factory() as session:
        contract = (await session.execute(select(Contract).where(Contract.external_number == number))).scalar_one()
        contract_id = contract.id
        # Сгенерированный график: 3 периода по 4000 (01.02/01.03/01.04.2026).
        # Платёж 2000 на 01.02 не закрывает даже первый период полностью.
        await recompute_contract(session, contract, upto_date=upto_date, grace_days=grace_days)
        await session.commit()

    async with session_factory() as session:
        snapshot = await session.get(DelinquencyDaily, (contract_id, upto_date))
        generated_dpd = snapshot.days_past_due

    schedule_body = _csv_bytes(
        _SCHEDULE_HEADER,
        [
            [number, "1", "01.02.2026", "2000"],
            [number, "2", "01.03.2026", "5000"],
            [number, "3", "01.04.2026", "5000"],
        ],
    )
    response = await admin_client.post(
        "/api/imports/payment-schedule", files={"file": ("schedule.csv", schedule_body, "text/csv")}
    )
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        contract = await session.get(Contract, contract_id)
        assert contract.schedule_source == "imported"
        # Импортированный график: платёж 2000 ровно гасит первый период целиком.
        await recompute_contract(session, contract, upto_date=upto_date, grace_days=grace_days)
        await session.commit()

    async with session_factory() as session:
        snapshot = await session.get(DelinquencyDaily, (contract_id, upto_date))
        imported_dpd = snapshot.days_past_due

    assert generated_dpd != imported_dpd
    assert imported_dpd < generated_dpd  # импортированный график точнее отражает факт полного гашения первого периода


async def test_payment_schedule_import_unknown_contract_reports_error(admin_client, session_factory):
    await create_employee(session_factory, email="rm-schedule-err@example.com", role="admin")
    await login(admin_client, "rm-schedule-err@example.com")

    schedule_body = _csv_bytes(_SCHEDULE_HEADER, [["K-NOPE", "1", "01.02.2026", "1000"]])
    response = await admin_client.post(
        "/api/imports/payment-schedule", files={"file": ("schedule.csv", schedule_body, "text/csv")}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["inserted"] == 0
    assert any("K-NOPE" in e for e in result["errors"])


async def test_reimporting_same_payment_schedule_file_is_idempotent(admin_client, session_factory):
    await create_employee(session_factory, email="rm-schedule-idem@example.com", role="admin")
    await login(admin_client, "rm-schedule-idem@example.com")

    number = f"K-{uuid.uuid4().hex[:8]}"
    contracts_body = _csv_bytes(
        _CONTRACTS_HEADER,
        [[number, "6000", "0", "6000", "0", "3", "01.01.2026", "Действует", "", "", ""]],
    )
    await admin_client.post("/api/imports/contracts", files={"file": ("contracts.csv", contracts_body, "text/csv")})

    schedule_body = _csv_bytes(_SCHEDULE_HEADER, [[number, "1", "01.02.2026", "2000"]])
    first = await admin_client.post(
        "/api/imports/payment-schedule", files={"file": ("schedule.csv", schedule_body, "text/csv")}
    )
    second = await admin_client.post(
        "/api/imports/payment-schedule", files={"file": ("schedule.csv", schedule_body, "text/csv")}
    )
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["batch_id"] == second.json()["batch_id"]
