"""T15: импорт договоров/платежей/исторических заявок (ТЗ «Договоры и платежи»,
«Логирование под будущую модель»). Формат выгрузки неизвестен заранее —
соответствие колонок только в config/import_mappings/*.yaml, этот модуль не
знает реальных названий колонок.

Контракт с вызывающим кодом (app/api/imports.py): все import_* функции
принимают уже открытую AsyncSession и сырые байты файла, сами коммитят в
конце (одна транзакция на пакет) и возвращают ImportResult. Идемпотентность —
по sha256 файла (app.models.ImportBatch.file_sha256 UNIQUE): повторная
загрузка того же файла возвращает уже существующий пакет, ничего не создавая
заново (критерий приёмки ТЗ)."""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Application,
    Client,
    Contract,
    ContractLinkCandidate,
    ImportBatch,
    Payment,
    PaymentSchedule,
)
from app.sources.name_normalize import normalize_name
from app.storage.base import Storage


class ImportFileInvalid(Exception):
    """Файл нечитаем (не CSV / битая кодировка) — 400 на уровне API, пакет не создаётся вовсе."""


@dataclass
class ImportResult:
    batch_id: UUID
    created: bool  # False — файл уже был загружен раньше (тот же sha256), дубликат не создан
    row_count: int
    inserted: int
    unmatched_contracts: list[dict[str, Any]] = field(default_factory=list)
    candidates_created: int = 0
    errors: list[str] = field(default_factory=list)
    # T15, правка после первой версии: действующие договоры, отсутствующие в
    # выгрузке ДОГОВОРОВ на ту же дату, что и этот пакет платежей (не в файле
    # платежей самом — у просрочника, который не платит, строк там просто нет,
    # это не признак неполноты, см. import_payments).
    missing_active_contracts: list[str] = field(default_factory=list)
    # T15, правка после первой версии: повторная выгрузка договоров — сколько
    # строк обновили уже существующий договор, а не создали новый.
    updated: int = 0


def _load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileInvalid("файл не в UTF-8 и не в CP1251 — не удалось прочитать")


def _parse_rows(raw: bytes) -> list[dict[str, str]]:
    text = _decode(raw)
    try:
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    except csv.Error as exc:
        raise ImportFileInvalid(f"файл не читается как CSV: {exc}") from exc


def _parse_date(raw: str, fmt: str) -> date:
    return datetime.strptime(raw.strip(), fmt).date()


def _parse_decimal(raw: str) -> Decimal:
    cleaned = raw.strip().replace(" ", "").replace(",", ".")
    return Decimal(cleaned)


def _digits_only(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = d.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1  # переполнение дня (31 января + 1 мес. -> нет 31 февраля) — последний день месяца


async def _get_or_create_batch(
    session: AsyncSession, *, kind: str, filename: str, raw: bytes, uploaded_by: UUID, storage: Storage,
    data_as_of: date | None = None,
) -> tuple[ImportBatch, bool]:
    """Возвращает (пакет, is_new). is_new=False — файл с таким sha256 уже
    загружался, существующий пакет возвращается без изменений (идемпотентность)."""
    file_hash = hashlib.sha256(raw).hexdigest()
    existing = (
        await session.execute(select(ImportBatch).where(ImportBatch.file_sha256 == file_hash))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    storage_key = await storage.save(f"imports/{kind}/{file_hash}.csv", raw)
    batch = ImportBatch(
        kind=kind, filename=filename, file_sha256=file_hash, storage_key=storage_key,
        uploaded_by=uploaded_by, status="processing", data_as_of=data_as_of,
    )
    session.add(batch)
    await session.flush()
    return batch, True


async def _find_contract_link_candidates(
    session: AsyncSession, *, applicant_name: str | None, applicant_phone: str | None,
) -> list[tuple[Application, str]]:
    """Кандидаты по телефону/ФИО — НИКОГДА не применяются автоматически (ваше
    явное указание: неверная привязка отдаёт модели чужой исход, это хуже
    отсутствия привязки). Возвращает (заявка, причина) для каждого найденного
    клиента — подтверждает человек через app/api/imports.py."""
    candidates: dict[UUID, str] = {}
    phone_digits = _digits_only(applicant_phone) if applicant_phone else None
    name_norm = normalize_name(applicant_name) if applicant_name else None

    clients: list[Client] = []
    if phone_digits or name_norm:
        clients = (
            await session.execute(select(Client))
        ).scalars().all()  # отфильтруем в Python — телефоны в БД не нормализованы единым форматом

    matched_clients: list[tuple[Client, str]] = []
    for client in clients:
        reasons = []
        if phone_digits and _digits_only(client.phone).endswith(phone_digits[-10:]):
            reasons.append("телефон")
        if name_norm and normalize_name(f"{client.last_name} {client.first_name}") == name_norm:
            reasons.append("ФИО")
        if reasons:
            matched_clients.append((client, "+".join(reasons)))

    for client, reason in matched_clients:
        apps = (
            await session.execute(select(Application).where(Application.client_id == client.id))
        ).scalars().all()
        for app in apps:
            candidates[app.id] = reason

    return [(await session.get(Application, app_id), reason) for app_id, reason in candidates.items()]


async def import_contracts(
    session: AsyncSession, *, raw: bytes, filename: str, uploaded_by: UUID, storage: Storage,
    mapping_path: str, data_as_of: date | None = None,
) -> ImportResult:
    """data_as_of — необязательная (обратная совместимость с разовой
    первоначальной загрузкой без даты): нужна только для того, чтобы
    import_payments мог сопоставить пакет платежей с пакетом договоров на ту
    же дату при проверке полноты выгрузки (ваше явное указание после первой
    версии предупреждения — см. комментарий там же).

    Повторная выгрузка договоров (та же учётная система, более поздняя дата,
    уже встречавшиеся external_number) — ОБНОВЛЯЕТ существующий договор
    (статус/закрытие/суммы), не создаёт дубликат и не падает на уникальности
    external_number: периодическая выгрузка договоров описывает текущее
    состояние, а не разовый факт (contracts не входит в append-only список
    правила 1 CLAUDE.md). График/кандидаты на привязку при обновлении не
    трогаются — это относится только к первому появлению договора."""
    mapping = _load_yaml(mapping_path)
    columns = mapping["columns"]
    status_map = mapping.get("status_map", {})
    closure_outcome_map = mapping.get("closure_outcome_map", {})
    date_format = mapping.get("date_format", "%Y-%m-%d")

    batch, is_new = await _get_or_create_batch(
        session, kind="contracts", filename=filename, raw=raw, uploaded_by=uploaded_by, storage=storage,
        data_as_of=data_as_of,
    )
    if not is_new:
        return ImportResult(batch_id=batch.id, created=False, row_count=batch.row_count or 0, inserted=0)

    try:
        rows = _parse_rows(raw)
    except ImportFileInvalid as exc:
        batch.status = "failed"
        batch.error_text = str(exc)
        await session.commit()
        raise

    result = ImportResult(batch_id=batch.id, created=True, row_count=len(rows), inserted=0)

    for i, row in enumerate(rows):
        try:
            external_number = row.get(columns["external_number"], "").strip() or None
            principal = _parse_decimal(row[columns["principal"]])
            markup = _parse_decimal(row[columns["markup"]])
            total_amount = _parse_decimal(row[columns["total_amount"]])
            down_payment = _parse_decimal(row[columns["down_payment"]])
            term_months = int(row[columns["term_months"]].strip())
            issued_at = _parse_date(row[columns["issued_at"]], date_format)
            raw_status = row.get(columns.get("status", ""), "").strip()
            status = status_map.get(raw_status, "active")

            closed_at_raw = row.get(columns.get("closed_at", ""), "").strip()
            closed_at = _parse_date(closed_at_raw, date_format) if closed_at_raw else None
            closure_reason = row.get(columns.get("closure_reason", ""), "").strip() or None
            # Закрытие — не всегда погашение (ваше замечание): трактовка причины
            # закрытия только из конфига. closed_at есть, а причина не
            # распознана — "unknown" (таргет NULL, не 0, см. target_bad_60_6/90_12).
            closure_outcome = (
                closure_outcome_map.get(closure_reason, "unknown") if closed_at is not None else None
            )
        except Exception as exc:  # noqa: BLE001 — одна плохая строка не должна ронять весь пакет
            result.errors.append(f"строка {i + 2}: {exc}")
            continue

        existing = (
            (
                await session.execute(select(Contract).where(Contract.external_number == external_number))
            ).scalar_one_or_none()
            if external_number is not None
            else None
        )
        if existing is not None:
            # Повторная выгрузка — текущее состояние того же договора, не
            # новый договор. График/кандидаты на привязку не трогаем (это
            # относится только к первому появлению договора); бьём только
            # поля, которые периодическая выгрузка реально описывает.
            existing.principal = principal
            existing.markup = markup
            existing.total_amount = total_amount
            existing.down_payment = down_payment
            existing.term_months = term_months
            existing.issued_at = issued_at
            existing.status = status
            existing.closed_at = closed_at
            existing.closure_reason = closure_reason
            existing.closure_outcome = closure_outcome
            existing.import_batch_id = batch.id
            result.updated += 1
            continue

        application_id: UUID | None = None
        match_column = columns.get("match_external_id")
        if match_column:
            external_ref = row.get(match_column, "").strip()
            if external_ref:
                application = (
                    await session.execute(select(Application).where(Application.external_id == external_ref))
                ).scalar_one_or_none()
                if application is not None:
                    application_id = application.id

        contract = Contract(
            application_id=application_id,
            external_number=external_number,
            principal=principal, markup=markup, total_amount=total_amount, down_payment=down_payment,
            term_months=term_months, issued_at=issued_at, status=status,
            closed_at=closed_at, closure_reason=closure_reason, closure_outcome=closure_outcome,
            import_batch_id=batch.id,
        )
        session.add(contract)
        await session.flush()
        result.inserted += 1

        _generate_schedule(session, contract)

        if application_id is None:
            applicant_name = row.get(columns.get("applicant_name", ""), "").strip() or None
            applicant_phone = row.get(columns.get("applicant_phone", ""), "").strip() or None
            candidates = await _find_contract_link_candidates(
                session, applicant_name=applicant_name, applicant_phone=applicant_phone,
            )
            for candidate_app, reason in candidates:
                session.add(
                    ContractLinkCandidate(
                        contract_id=contract.id, application_id=candidate_app.id, match_reason=reason,
                        source_name=applicant_name, source_phone=applicant_phone,
                    )
                )
                result.candidates_created += 1
            result.unmatched_contracts.append(
                {
                    "contract_id": str(contract.id), "external_number": external_number,
                    "applicant_name": applicant_name, "applicant_phone": applicant_phone,
                    "candidates_found": len(candidates),
                }
            )

    batch.status = "completed"
    batch.row_count = len(rows)
    await session.commit()
    return result


def _generate_schedule(session: AsyncSession, contract: Contract) -> None:
    """Запасной вариант (ваше замечание): равные ежемесячные платежи, если для
    договора НЕ пришёл отдельный файл графика (import_payment_schedule ниже).
    Параметры рассрочки (сумма, срок, дата выдачи) у нас уже есть из самого
    договора — для запасного варианта этого достаточно."""
    principal_to_schedule = contract.total_amount - contract.down_payment
    term = contract.term_months
    base = (principal_to_schedule / term).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    amounts = [base] * term
    amounts[-1] += principal_to_schedule - base * term  # остаток округления — в последний период

    for period_no in range(1, term + 1):
        due_date = _add_months(contract.issued_at, period_no)
        session.add(
            PaymentSchedule(
                contract_id=contract.id, period_no=period_no, due_date=due_date,
                due_amount=amounts[period_no - 1],
            )
        )


async def import_payment_schedule(
    session: AsyncSession, *, raw: bytes, filename: str, uploaded_by: UUID, storage: Storage,
    mapping_path: str,
) -> ImportResult:
    """Ваше замечание: график — отдельным импортом, со своим маппингом
    колонок; генерация равными платежами — только запасной вариант. Если для
    договора пришёл файл графика, он ЗАМЕНЯЕТ сгенерированный (payment_schedule
    не входит в append-only список правила 1 CLAUDE.md — это не факт-лог, а
    условие сделки, которое мы либо знаем точно из выгрузки, либо
    аппроксимируем сами) и ставит schedule_source="imported"."""
    mapping = _load_yaml(mapping_path)
    columns = mapping["columns"]
    date_format = mapping.get("date_format", "%Y-%m-%d")

    batch, is_new = await _get_or_create_batch(
        session, kind="payment_schedule", filename=filename, raw=raw, uploaded_by=uploaded_by, storage=storage,
    )
    if not is_new:
        return ImportResult(batch_id=batch.id, created=False, row_count=batch.row_count or 0, inserted=0)

    try:
        rows = _parse_rows(raw)
    except ImportFileInvalid as exc:
        batch.status = "failed"
        batch.error_text = str(exc)
        await session.commit()
        raise

    result = ImportResult(batch_id=batch.id, created=True, row_count=len(rows), inserted=0)

    # Группируем по договору — заменяем график целиком одним разом на
    # договор, а не построчно (иначе первая же строка файла удалит периоды,
    # которые добавит вторая).
    rows_by_contract: dict[str, list[dict[str, str]]] = {}
    for i, row in enumerate(rows):
        try:
            external_number = row[columns["external_number"]].strip()
        except KeyError as exc:
            result.errors.append(f"строка {i + 2}: {exc}")
            continue
        rows_by_contract.setdefault(external_number, []).append(row)

    for external_number, contract_rows in rows_by_contract.items():
        contract = (
            await session.execute(select(Contract).where(Contract.external_number == external_number))
        ).scalar_one_or_none()
        if contract is None:
            result.errors.append(f"договор {external_number!r} не найден (график без договора)")
            continue

        parsed_periods: list[PaymentSchedule] = []
        row_had_error = False
        for row in contract_rows:
            try:
                period_no = int(row[columns["period_no"]].strip())
                due_date = _parse_date(row[columns["due_date"]], date_format)
                due_amount = _parse_decimal(row[columns["due_amount"]])
            except (KeyError, ValueError) as exc:
                result.errors.append(f"договор {external_number!r}: {exc}")
                row_had_error = True
                continue
            parsed_periods.append(
                PaymentSchedule(contract_id=contract.id, period_no=period_no, due_date=due_date, due_amount=due_amount)
            )

        if not parsed_periods:
            continue

        existing = (
            await session.execute(select(PaymentSchedule).where(PaymentSchedule.contract_id == contract.id))
        ).scalars().all()
        for row_item in existing:
            await session.delete(row_item)
        await session.flush()

        for item in parsed_periods:
            session.add(item)
        contract.schedule_source = "imported"
        result.inserted += len(parsed_periods)
        if row_had_error:
            result.errors.append(f"договор {external_number!r}: график заменён частично (были ошибки в строках)")

    batch.status = "completed"
    batch.row_count = len(rows)
    await session.commit()
    return result


async def import_payments(
    session: AsyncSession, *, raw: bytes, filename: str, uploaded_by: UUID, storage: Storage,
    mapping_path: str, data_as_of: date, contracts_mapping_path: str,
) -> ImportResult:
    """contracts_mapping_path — нужен только для предупреждения о полноте
    выгрузки (см. ниже), сами платежи им не парсятся."""
    mapping = _load_yaml(mapping_path)
    columns = mapping["columns"]
    kind_map = mapping.get("kind_map", {})
    date_format = mapping.get("date_format", "%Y-%m-%d")

    batch, is_new = await _get_or_create_batch(
        session, kind="payments", filename=filename, raw=raw, uploaded_by=uploaded_by, storage=storage,
        data_as_of=data_as_of,
    )
    if not is_new:
        return ImportResult(batch_id=batch.id, created=False, row_count=batch.row_count or 0, inserted=0)

    try:
        rows = _parse_rows(raw)
    except ImportFileInvalid as exc:
        batch.status = "failed"
        batch.error_text = str(exc)
        await session.commit()
        raise

    result = ImportResult(batch_id=batch.id, created=True, row_count=len(rows), inserted=0)
    kind_column = columns.get("kind")

    for i, row in enumerate(rows):
        try:
            external_number = row[columns["external_number"]].strip()
            paid_at = _parse_date(row[columns["paid_at"]], date_format)
            # Всегда модуль суммы — знак/смысл (гасит график или отменяет
            # предыдущий платёж) несёт только kind, не само число (выгрузка
            # может отдавать сторно и положительным, и отрицательным числом).
            amount = abs(_parse_decimal(row[columns["amount"]]))
            raw_kind = row.get(kind_column, "").strip() if kind_column else ""
            kind = kind_map.get(raw_kind, "regular")
        except (KeyError, ValueError) as exc:
            result.errors.append(f"строка {i + 2}: {exc}")
            continue

        contract = (
            await session.execute(select(Contract).where(Contract.external_number == external_number))
        ).scalar_one_or_none()
        if contract is None:
            result.errors.append(f"строка {i + 2}: договор {external_number!r} не найден")
            continue

        session.add(
            Payment(
                contract_id=contract.id, paid_at=paid_at, amount=amount, source="import", kind=kind,
                import_batch_id=batch.id,
            )
        )
        result.inserted += 1

    result.missing_active_contracts = await _missing_active_contracts(
        session, storage=storage, payments_data_as_of=data_as_of, contracts_mapping_path=contracts_mapping_path,
    )

    batch.status = "completed"
    batch.row_count = len(rows)
    await session.commit()
    return result


async def _missing_active_contracts(
    session: AsyncSession, *, storage: Storage, payments_data_as_of: date, contracts_mapping_path: str,
) -> list[str]:
    """Правка после первой версии (ваше замечание): предупреждение о
    неполноте выгрузки платежей нельзя строить на наличии строк в САМОМ
    файле платежей — у договора, который действующий, но просто ничего не
    платит, строк там нет вовсе, и предупреждение срабатывало бы ровно на
    просрочниках (оператор привык бы его игнорировать). Вместо этого пакет
    платежей сопоставляется с пакетом ДОГОВОРОВ на ТУ ЖЕ дату
    (import_batches.data_as_of у kind='contracts') — предупреждение только
    если действующий договор отсутствует в этой выгрузке договоров.

    Если выгрузки договоров с такой датой нет (например, договоры загружены
    один раз без даты, а платежи — периодически), сверить не с чем —
    предупреждение не выдаётся вовсе, не угадывается по файлу платежей. Чтобы
    предупреждение вообще заработало, оператор должен загружать выгрузку
    договоров той же датой, что и выгрузку платежей (см. docs/payments_import.md)."""
    contracts_batch = (
        await session.execute(
            select(ImportBatch)
            .where(
                ImportBatch.kind == "contracts", ImportBatch.status == "completed",
                ImportBatch.data_as_of == payments_data_as_of,
            )
            .order_by(ImportBatch.uploaded_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if contracts_batch is None:
        return []

    contracts_mapping = _load_yaml(contracts_mapping_path)
    external_number_column = contracts_mapping["columns"]["external_number"]
    contracts_raw = await storage.read(contracts_batch.storage_key)
    exported_numbers = {
        row.get(external_number_column, "").strip()
        for row in _parse_rows(contracts_raw)
        if row.get(external_number_column, "").strip()
    }

    active_numbers = (
        await session.execute(select(Contract.external_number).where(Contract.status == "active"))
    ).scalars().all()
    return sorted(
        number for number in active_numbers if number is not None and number not in exported_numbers
    )


async def import_historical_applications(
    session: AsyncSession, *, raw: bytes, filename: str, uploaded_by: UUID, storage: Storage,
    mapping_path: str,
) -> ImportResult:
    """Без feature_snapshots/decisions — для исторических заявок их нет (ТЗ,
    «Логирование под будущую модель»: отказники — половина ценности датасета,
    но мы не придумываем признаки заднем числом, которых не вычисляли)."""
    mapping = _load_yaml(mapping_path)
    columns = mapping["columns"]
    status_map = mapping.get("status_map", {})
    date_format = mapping.get("date_format", "%Y-%m-%d")

    batch, is_new = await _get_or_create_batch(
        session, kind="historical_applications", filename=filename, raw=raw, uploaded_by=uploaded_by,
        storage=storage,
    )
    if not is_new:
        return ImportResult(batch_id=batch.id, created=False, row_count=batch.row_count or 0, inserted=0)

    try:
        rows = _parse_rows(raw)
    except ImportFileInvalid as exc:
        batch.status = "failed"
        batch.error_text = str(exc)
        await session.commit()
        raise

    result = ImportResult(batch_id=batch.id, created=True, row_count=len(rows), inserted=0)

    for i, row in enumerate(rows):
        try:
            external_ref = row.get(columns.get("external_reference", ""), "").strip()
            external_id = f"HIST-{batch.file_sha256[:8]}-{external_ref or i}"
            last_name = row.get(columns.get("last_name", ""), "").strip()
            first_name = row.get(columns.get("first_name", ""), "").strip()
            goods_name = row.get(columns.get("goods_name", ""), "").strip() or "не указано"
            goods_price = _parse_decimal(row[columns["goods_price"]]) if row.get(columns.get("goods_price", "")) else Decimal("0")
            term_months = int(row[columns["term_months"]].strip()) if row.get(columns.get("term_months", "")) else 0
            tariff = row.get(columns.get("tariff", ""), "").strip() or "unknown"
            submitted_at_raw = row.get(columns.get("submitted_at", ""), "").strip()
            submitted_at = (
                datetime.combine(_parse_date(submitted_at_raw, date_format), datetime.min.time(), tzinfo=timezone.utc)
                if submitted_at_raw else datetime.now(timezone.utc)
            )
            raw_outcome = row.get(columns.get("outcome", ""), "").strip()
            status = status_map.get(raw_outcome, "rejected_a")
        except (KeyError, ValueError) as exc:
            result.errors.append(f"строка {i + 2}: {exc}")
            continue

        existing = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one_or_none()
        if existing is not None:
            continue  # та же строка того же пакета (повторный sha256 уже отсечён выше) — защита на всякий случай

        session.add(
            Application(
                external_id=external_id, status=status, source="historical_import",
                goods_name=goods_name, goods_price=goods_price, down_payment=Decimal("0"),
                term_months=term_months, tariff=tariff, requested_amount=goods_price,
                profile={"last_name": last_name, "first_name": first_name, "_historical": True},
                consent_pd_at=submitted_at, submitted_at=submitted_at,
            )
        )
        result.inserted += 1

    batch.status = "completed"
    batch.row_count = len(rows)
    await session.commit()
    return result
