"""T15, ваше явное указание (п.4 правок): docs/demo_import/*.csv — набор для
ручной проверки через экран «Импорт данных» (контракты/график/платежи на 5-6
договоров с заранее известной историей). Этот тест прогоняет ТЕ ЖЕ САМЫЕ файлы
через настоящий импорт + пересчёт просрочки + вью таргета и утверждает ровно
те цифры, что задокументированы в docs/demo_import/expected.md — чтобы они не
могли разойтись молча, если кто-то поменяет движок просрочки или вью.

upto_date передаётся явно (2026-10-02, дата составления примера), а не берётся
из реального "сегодня" — иначе числа по ещё не закрытым договорам (которые
продолжают накапливать просрочку) менялись бы в зависимости от того, когда
запущен pytest. Это ограничение явно описано в expected.md: при ручной
проверке оператором в другой день открытые договоры покажут БОЛЬШУЮ просрочку,
закрытые (EARLYPAY/REPO) — нет, это зафиксировано closure_outcome."""

from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.engine.delinquency_recompute import load_grace_days, recompute_contract
from app.models import Contract, DelinquencyDaily
from tests._admin_helpers import create_employee, login

DEMO_DIR = Path("/app/docs/demo_import")
REFERENCE_DATE = date(2026, 10, 2)


async def _upload(admin_client, path: str, filename: str, content: bytes, **form) -> dict:
    response = await admin_client.post(
        f"/api/imports/{path}", data=form, files={"file": (filename, content, "text/csv")}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _recompute_and_read(session_factory, *, external_number: str, upto_date: date) -> DelinquencyDaily:
    grace_days = load_grace_days()
    async with session_factory() as session:
        contract = (
            await session.execute(select(Contract).where(Contract.external_number == external_number))
        ).scalar_one()
        await recompute_contract(session, contract, upto_date=upto_date, grace_days=grace_days)
        await session.commit()
        contract_id = contract.id

    async with session_factory() as session:
        snapshot = await session.get(DelinquencyDaily, (contract_id, upto_date))
        assert snapshot is not None
        return snapshot


async def _target(session_factory, *, external_number: str, view: str) -> tuple[int | None, bool]:
    async with session_factory() as session:
        contract_id = (
            await session.execute(select(Contract.id).where(Contract.external_number == external_number))
        ).scalar_one()
        from sqlalchemy import text

        row = (
            await session.execute(text(f"SELECT bad, is_mature FROM {view} WHERE contract_id = :cid"), {"cid": contract_id})
        ).one()
        return row.bad, row.is_mature


async def test_demo_import_fixtures_match_expected_md(admin_client, session_factory):
    await create_employee(session_factory, email="rm-demo@example.com", role="risk_manager")
    await login(admin_client, "rm-demo@example.com")

    contracts_result = await _upload(
        admin_client, "contracts", "contracts.csv", (DEMO_DIR / "contracts.csv").read_bytes(),
        data_as_of=REFERENCE_DATE.isoformat(),
    )
    assert contracts_result["errors"] == []
    assert contracts_result["inserted"] == 6

    schedule_result = await _upload(
        admin_client, "payment-schedule", "payment_schedule.csv", (DEMO_DIR / "payment_schedule.csv").read_bytes()
    )
    assert schedule_result["errors"] == []
    assert schedule_result["inserted"] == 6 * 6 - 6  # DEMO-PARTIAL/DEMO-REVERSED — по 3 периода, остальные по 6

    payments_result = await _upload(
        admin_client, "payments", "payments.csv", (DEMO_DIR / "payments.csv").read_bytes(),
        data_as_of=REFERENCE_DATE.isoformat(),
    )
    assert payments_result["errors"] == []
    # Правка после первой версии: предупреждение сверяется с выгрузкой
    # ДОГОВОРОВ на ту же дату, не с файлом платежей. DEMO-LATE70 не платит ни
    # разу (п.4: "просрочил на 70 дней"), но ОН ЕСТЬ в contracts.csv (та же
    # дата 2026-10-02) — предупреждения быть не должно (это и есть п.3 этой
    # правки: "договор без единого платежа, но присутствующий в файле
    # договоров, предупреждения не вызывает").
    assert payments_result["missing_active_contracts"] == []

    async with session_factory() as session:
        for number in ("DEMO-ONTIME", "DEMO-LATE70", "DEMO-PARTIAL", "DEMO-EARLYPAY", "DEMO-REPO", "DEMO-REVERSED"):
            contract = (
                await session.execute(select(Contract).where(Contract.external_number == number))
            ).scalar_one()
            assert contract.schedule_source == "imported", number

    # DEMO-ONTIME: платит вовремя -> без просрочки, таргет 0 (6мес., окно покрыто данными).
    snap = await _recompute_and_read(session_factory, external_number="DEMO-ONTIME", upto_date=REFERENCE_DATE)
    assert (snap.days_past_due, snap.overdue_amount) == (0, 0)
    assert await _target(session_factory, external_number="DEMO-ONTIME", view="target_bad_60_6") == (0, True)

    # DEMO-LATE70: просрочил на 70 дней (период 19.07.2026 так и не оплачен) -> bad=1 по 60/6 уже сейчас,
    # хотя по 12-месячному окну ещё рано (90+ дней там не достигнуто).
    snap = await _recompute_and_read(session_factory, external_number="DEMO-LATE70", upto_date=REFERENCE_DATE)
    assert (snap.days_past_due, snap.overdue_amount) == (70, 6000)
    assert await _target(session_factory, external_number="DEMO-LATE70", view="target_bad_60_6") == (1, True)
    assert await _target(session_factory, external_number="DEMO-LATE70", view="target_bad_90_12") == (None, False)

    # DEMO-PARTIAL: частично заплатил (1200 из 2000) -> просрочка есть, но мало (26 дн.) -> таргет ещё NULL.
    snap = await _recompute_and_read(session_factory, external_number="DEMO-PARTIAL", upto_date=REFERENCE_DATE)
    assert (snap.days_past_due, snap.overdue_amount) == (26, 800)
    assert await _target(session_factory, external_number="DEMO-PARTIAL", view="target_bad_60_6") == (None, False)

    # DEMO-EARLYPAY: погасил досрочно -> без просрочки, closure_outcome=good -> таргет 0.
    snap = await _recompute_and_read(session_factory, external_number="DEMO-EARLYPAY", upto_date=REFERENCE_DATE)
    assert (snap.days_past_due, snap.overdue_amount) == (0, 0)
    assert await _target(session_factory, external_number="DEMO-EARLYPAY", view="target_bad_60_6") == (0, True)

    # DEMO-REPO: товар изъят до первого платежа по графику (0 дней просрочки по факту) ->
    # closure_outcome=bad -> таргет 1, несмотря на отсутствие формальной просрочки.
    snap = await _recompute_and_read(session_factory, external_number="DEMO-REPO", upto_date=REFERENCE_DATE)
    assert (snap.days_past_due, snap.overdue_amount) == (0, 0)
    assert await _target(session_factory, external_number="DEMO-REPO", view="target_bad_60_6") == (1, True)

    # DEMO-REVERSED: второй платёж сторнирован -> период снова считается неоплаченным (26 дн., 2000).
    snap = await _recompute_and_read(session_factory, external_number="DEMO-REVERSED", upto_date=REFERENCE_DATE)
    assert (snap.days_past_due, snap.overdue_amount) == (26, 2000)
    assert await _target(session_factory, external_number="DEMO-REVERSED", view="target_bad_60_6") == (None, False)
