"""Движок просрочки (T15, ТЗ «Договоры и платежи»). Чистая функция: график +
платежи -> дни просрочки/сумма просрочки на дату. Без БД и сети — тем же
принципом, что app/engine/rules.py.

Алгоритм — платёж гасит сначала самый старый неоплаченный взнос (ваше явное
указание): считаем общую сумму зачтённых платежей на дату и разносим её по
периодам графика от самого старого к самому новому. Частичный платёж
оставляет остаток на этом периоде; переплата/досрочное погашение уходит на
следующие периоды; если денег не хватило на период — все последующие периоды
остаются должными полностью (это гарантирует, что просроченные периоды всегда
непрерывный "хвост" графика с самого старого).

Что считается "зачтённым платежом" — решает вызывающий код (app/engine/imports.py
/ app/tasks/delinquency.py), не этот модуль:
- kind="regular" — учитывается знаком (+).
- kind="penalty" — НЕ передаётся сюда вовсе (неустойка не гасит график, ваше
  явное указание — на благотворительность, не наш доход).
- kind="reversal" — учитывается знаком (-): отменяет ранее зачтённый платёж,
  не удаляя и не меняя исходную запись (payments append-only).

Льготный срok (grace_days, config/collections.yaml) — дни просрочки и сумма
"overdue" считаются по датам, уже сдвинутым на него: days_past_due =
max(0, (as_of - due_date самого старого непогашенного периода) - grace_days).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class ScheduleItem:
    period_no: int
    due_date: date
    due_amount: Decimal


@dataclass(frozen=True)
class DelinquencySnapshot:
    days_past_due: int
    overdue_amount: Decimal


def allocate_payments(schedule: list[ScheduleItem], total_paid: Decimal) -> list[Decimal]:
    """Остаток долга по каждому периоду графика (в порядке period_no) после
    разнесения total_paid от самого старого периода к самому новому."""
    remaining: list[Decimal] = []
    pool = total_paid
    for item in sorted(schedule, key=lambda s: s.period_no):
        applied = min(pool, item.due_amount) if pool > 0 else Decimal("0")
        remaining.append(item.due_amount - applied)
        pool -= applied
    return remaining


def compute_delinquency(
    schedule: list[ScheduleItem],
    payments: list[tuple[date, Decimal]],
    as_of: date,
    grace_days: int,
) -> DelinquencySnapshot:
    """payments — уже отфильтрованные и подписанные зачтённые суммы (regular
    (+) и reversal (-) их отменяющие; penalty сюда не передаётся вовсе)."""
    total_paid = sum((amount for paid_at, amount in payments if paid_at <= as_of), Decimal("0"))
    ordered_schedule = sorted(schedule, key=lambda s: s.period_no)
    remaining = allocate_payments(ordered_schedule, total_paid)

    overdue_amount = Decimal("0")
    oldest_overdue_due_date: date | None = None
    for item, balance in zip(ordered_schedule, remaining):
        if balance <= 0:
            continue
        days_since_due = (as_of - item.due_date).days
        if days_since_due <= grace_days:
            continue  # ещё в пределах льготного срока (или период в будущем) — не просрочка
        overdue_amount += balance
        if oldest_overdue_due_date is None or item.due_date < oldest_overdue_due_date:
            oldest_overdue_due_date = item.due_date

    if oldest_overdue_due_date is None:
        return DelinquencySnapshot(days_past_due=0, overdue_amount=Decimal("0"))

    days_past_due = (as_of - oldest_overdue_due_date).days - grace_days
    return DelinquencySnapshot(days_past_due=days_past_due, overdue_amount=overdue_amount)


def compute_delinquency_series(
    schedule: list[ScheduleItem],
    payments: list[tuple[date, Decimal]],
    start_date: date,
    end_date: date,
    grace_days: int,
) -> dict[date, DelinquencySnapshot]:
    """Снимок на каждый день [start_date, end_date] включительно — для
    первичного наполнения/полного пересчёта delinquency_daily."""
    result: dict[date, DelinquencySnapshot] = {}
    current = start_date
    while current <= end_date:
        result[current] = compute_delinquency(schedule, payments, current, grace_days)
        current += timedelta(days=1)
    return result
