"""Портфель демо-сида (T23): CSV в форматах config/import_mappings/*.yaml —
грузятся штатным импортом T15 (POST /api/imports/*), как выгрузка из учётной
системы заказчика. Чистые функции, без БД.

За 60 дней демо-заявок просрочка 30–60 и 90+ физически не успевает
набежать (первый платёж — через месяц после выдачи). Поэтому к договорам по
одобренным демо-заявкам добавлены договоры по нескольким ИСТОРИЧЕСКИМ заявкам
(4–10 месяцев назад, импорт исторических заявок T15) — именно они дают
просрочку 30–60/90+ и закрытия «погашен»/«товар изъят»."""

import csv
import hashlib
import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.demo.scenarios import DemoPlan

_DATE = "%d.%m.%Y"

ON_TIME = "on_time"
LATE_1_30 = "late_1_30"
LATE_30_60 = "late_30_60"
LATE_90 = "late_90_plus"
CLOSED_EARLY = "closed_early"
CLOSED_PAID = "closed_paid"
CLOSED_REPOSSESSED = "closed_repossessed"
NOT_DUE_YET = "not_due_yet"


@dataclass
class DemoContract:
    number: str
    application_ref: str  # «Номер заявки»: external_id нашей заявки (или ref исторической)
    full_name: str
    phone: str
    principal: Decimal
    down_payment: Decimal
    term_months: int
    issued_at: date
    profile: str
    closed_at: date | None = None
    schedule: list[tuple[int, date, Decimal]] = field(default_factory=list)
    payments: list[tuple[date, Decimal, str]] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return self.principal - self.down_payment


@dataclass
class DemoPortfolio:
    historical_csv: bytes
    contracts_csv: bytes
    schedule_csv: bytes
    payments_csv: bytes
    contracts: list[DemoContract]


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def _build_schedule(contract: DemoContract) -> None:
    base = (contract.total / contract.term_months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rows = []
    for n in range(1, contract.term_months + 1):
        amount = base if n < contract.term_months else contract.total - base * (contract.term_months - 1)
        rows.append((n, _add_months(contract.issued_at, n), amount))
    contract.schedule = rows


def _pay_profile(contract: DemoContract, rng: random.Random, today: date) -> None:
    """Платежи по профилю; due — дата по графику, «сегодня» — дата выгрузки."""
    due_rows = [(n, d, a) for n, d, a in contract.schedule if d <= today]
    profile = contract.profile
    payments: list[tuple[date, Decimal, str]] = []

    if profile == CLOSED_EARLY:
        # Пара платежей по графику, затем досрочно — остаток целиком.
        paid = Decimal("0")
        for n, due, amount in due_rows[:2]:
            payments.append((due - timedelta(days=rng.randint(0, 3)), amount, "Платёж по графику"))
            paid += amount
        close = min(today - timedelta(days=rng.randint(1, 6)),
                    (due_rows[-1][1] if due_rows else contract.issued_at) + timedelta(days=rng.randint(5, 20)))
        close = max(close, contract.issued_at + timedelta(days=10))
        payments.append((close, contract.total - paid, "Досрочное погашение"))
        contract.closed_at = close
    elif profile == CLOSED_PAID:
        for n, due, amount in contract.schedule:
            payments.append((due - timedelta(days=rng.randint(0, 4)), amount, "Платёж по графику"))
        contract.closed_at = payments[-1][0]
    elif profile in (LATE_90, CLOSED_REPOSSESSED):
        # Платил первые 1–2 периода, потом перестал.
        for n, due, amount in due_rows[: rng.choice((1, 2))]:
            payments.append((due + timedelta(days=rng.randint(0, 6)), amount, "Платёж по графику"))
        if profile == CLOSED_REPOSSESSED:
            contract.closed_at = today - timedelta(days=rng.randint(10, 25))
    elif profile == LATE_30_60:
        # Последний период, просроченный на 30–60 дней, не оплачен.
        unpaid_from = next(
            (i for i, (_, due, _) in enumerate(due_rows) if 30 <= (today - due).days <= 60), len(due_rows) - 1
        )
        for n, due, amount in due_rows[:unpaid_from]:
            payments.append((due + timedelta(days=rng.randint(0, 8)), amount, "Платёж по графику"))
        if unpaid_from > 0 and rng.random() < 0.5:
            payments.append((today - timedelta(days=rng.randint(3, 15)), Decimal("500.00"), "Неустойка"))
    elif profile == LATE_1_30:
        for n, due, amount in due_rows[:-1]:
            payments.append((due + timedelta(days=rng.randint(0, 3)), amount, "Платёж по графику"))
        # Последний наступивший период — частично или не оплачен вовсе.
        if due_rows and rng.random() < 0.4:
            _, due, amount = due_rows[-1]
            payments.append((min(today, due + timedelta(days=1)), (amount / 3).quantize(Decimal("0.01")),
                             "Платёж по графику"))
    else:  # ON_TIME / NOT_DUE_YET
        for n, due, amount in due_rows:
            payments.append((due - timedelta(days=rng.choice((0, 0, 0, 1, 2, 5))), amount, "Платёж по графику"))
    contract.payments = [p for p in payments if p[0] <= today]


_HISTORICAL = (
    # (ref, месяцев назад, срок, профиль)
    ("H-0412", 10, 6, CLOSED_PAID),
    ("H-0436", 9, 6, CLOSED_PAID),
    ("H-0457", 8, 12, LATE_90),
    ("H-0471", 8, 10, CLOSED_REPOSSESSED),
    ("H-0488", 7, 12, LATE_90),
    ("H-0503", 6, 12, LATE_90),
    ("H-0519", 6, 12, LATE_30_60),
    ("H-0534", 5, 12, LATE_30_60),
    ("H-0548", 5, 9, LATE_30_60),
    ("H-0561", 4, 12, ON_TIME),
    ("H-0577", 4, 10, ON_TIME),
    ("H-0590", 4, 12, LATE_1_30),
)
_HISTORICAL_GOODS = (
    ("Холодильник Bosch KGN39", 58000), ("Диван «Честер»", 74000), ("Смартфон Samsung Galaxy S23", 69000),
    ("Шины R16, 4 шт.", 41000), ("Кухонный гарнитур", 186000), ("Ноутбук ASUS VivoBook", 64000),
    ("Стиральная машина Haier", 39000), ("Телевизор LG 50\"", 47000), ("Шкаф-купе", 52000),
    ("Генератор и стартер, комплект", 33000), ("Посудомоечная машина Midea", 36000),
    ("Смартфон Apple iPhone 13", 61000),
)
_HISTORICAL_NAMES = (
    ("Алибеков", "Заур", "Гамзатович"), ("Яковлева", "Ирина", "Павловна"), ("Мирзоев", "Рустам", "Ахмедович"),
    ("Сулейманова", "Диана", "Рашидовна"), ("Белов", "Артём", "Игоревич"), ("Гамзатов", "Ильяс", "Магомедович"),
    ("Абасова", "Камила", "Расуловна"), ("Захаров", "Олег", "Викторович"), ("Набиев", "Эмиль", "Шамилевич"),
    ("Казиева", "Луиза", "Омаровна"), ("Тимофеев", "Роман", "Сергеевич"), ("Дадаев", "Арслан", "Курбанович"),
)


def historical_external_id(historical_csv: bytes, ref: str) -> str:
    """Тот же external_id, что даст app/engine/imports.py::import_historical_applications."""
    return f"HIST-{hashlib.sha256(historical_csv).hexdigest()[:8]}-{ref}"


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def build_portfolio(plan: DemoPlan, *, today: date, seed: int = 2026) -> DemoPortfolio:
    rng = random.Random(seed + 7)

    # --- исторические заявки (только одобренные с договором) -----------------
    historical_rows = []
    historical_meta = []
    for i, (ref, months_ago, term, profile) in enumerate(_HISTORICAL):
        submitted = _add_months(today, -months_ago) - timedelta(days=rng.randint(0, 20))
        goods, price = _HISTORICAL_GOODS[i]
        last, first, middle = _HISTORICAL_NAMES[i]
        phone = f"+79990009{i:03d}"
        historical_rows.append([ref, last, first, middle, phone, goods, str(price), str(term), "standard",
                                submitted.strftime(_DATE), "Одобрено"])
        historical_meta.append((ref, submitted, Decimal(price), term, profile, f"{last} {first} {middle}", phone))
    historical_csv = _csv(
        ["Номер заявки", "Фамилия", "Имя", "Отчество", "Телефон", "Товар", "Сумма", "Срок мес", "Тариф",
         "Дата заявки", "Результат"],
        historical_rows,
    )

    contracts: list[DemoContract] = []
    number = 0
    for ref, submitted, price, term, profile, full_name, phone in historical_meta:
        number += 1
        contracts.append(DemoContract(
            number=f"МР-{submitted.year}-{number:04d}", application_ref=historical_external_id(historical_csv, ref),
            full_name=full_name, phone=phone, principal=price, down_payment=Decimal("0"), term_months=term,
            issued_at=submitted + timedelta(days=rng.randint(1, 4)), profile=profile,
        ))

    # --- договоры по одобренным демо-заявкам ---------------------------------
    approved = [a for a in plan.applications
                if a.expected_status == "approved" and a.contract_issue_date and a.contract_issue_date <= today]
    for app in approved:
        if rng.random() < 0.12:
            continue  # клиент не пришёл за товаром — одобрение без договора
        age = (today - app.contract_issue_date).days
        if age < 31:
            profile = CLOSED_EARLY if age > 14 and rng.random() < 0.12 else NOT_DUE_YET
        else:
            profile = rng.choices((ON_TIME, LATE_1_30, CLOSED_EARLY), weights=(70, 22, 8))[0]
        number += 1
        deal = app.deal
        contracts.append(DemoContract(
            number=f"МР-{app.contract_issue_date.year}-{number:04d}", application_ref=app.external_id,
            full_name=app.applicant.full_name, phone=app.applicant.phone, principal=deal.price,
            down_payment=deal.down_payment, term_months=deal.term_months, issued_at=app.contract_issue_date,
            profile=profile,
        ))

    for contract in contracts:
        _build_schedule(contract)
        _pay_profile(contract, rng, today)

    status_by_profile = {CLOSED_EARLY: "Закрыт", CLOSED_PAID: "Закрыт", CLOSED_REPOSSESSED: "Изъятие"}
    reason_by_profile = {CLOSED_EARLY: "Погашен досрочно", CLOSED_PAID: "Погашен", CLOSED_REPOSSESSED: "Товар изъят"}
    contracts_csv = _csv(
        ["Номер договора", "Сумма товара", "Наценка", "Итого к оплате", "Первый взнос", "Срок мес",
         "Дата выдачи", "Статус", "Дата закрытия", "Причина закрытия", "Номер заявки", "ФИО", "Телефон"],
        [
            [c.number, _money(c.principal), "0.00", _money(c.total), _money(c.down_payment), str(c.term_months),
             c.issued_at.strftime(_DATE), status_by_profile.get(c.profile, "Действует"),
             c.closed_at.strftime(_DATE) if c.closed_at else "", reason_by_profile.get(c.profile, ""),
             c.application_ref, c.full_name, c.phone]
            for c in contracts
        ],
    )
    schedule_csv = _csv(
        ["Номер договора", "№ периода", "Дата платежа", "Сумма"],
        [[c.number, str(n), d.strftime(_DATE), _money(a)] for c in contracts for n, d, a in c.schedule],
    )
    payments_csv = _csv(
        ["Номер договора", "Дата платежа", "Сумма", "Тип платежа"],
        [[c.number, d.strftime(_DATE), _money(a), kind] for c in contracts for d, a, kind in c.payments],
    )
    return DemoPortfolio(
        historical_csv=historical_csv, contracts_csv=contracts_csv, schedule_csv=schedule_csv,
        payments_csv=payments_csv, contracts=contracts,
    )
