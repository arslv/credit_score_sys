"""T14: симуляция новой версии конфига по сохранённым снимкам признаков —
без обращений к источникам, без записи в decisions/feature_snapshots (ТЗ,
«Конфиг» -> «Обязательная симуляция»).

Область действия — намеренно только сам движок правил (app.engine.rules):
evaluate_hard_stops (стадия А) -> evaluate_stage_b_pre_bki -> evaluate_financial
(стадия Б). Лимиты сделки/тарифы, согласие на КИ, поручитель и внешние
проверки НЕ переигрываются — это отдельная орбита конвейера (limits.py,
guarantor-логика в stage_b_pipeline.py), не "правила" в смысле этого экрана,
и часть из них не восстановима из одних только сохранённых признаков без
похода в БД за другими сущностями. Согласовано с вами при подтверждении
плана T14.

Три уточнения (ваши явные указания при подтверждении плана):
1. Сравниваем АКТИВНУЮ версию и кандидата на ОДНИХ И ТЕХ ЖЕ снимках — не
   фактическое решение (оно могло быть по старой версии конфига или
   переопределено оператором) и не другую версию, кроме активной сейчас.
   Расхождение с фактическим Decision — только в справочных полях
   *_actual_outcome/*_actual_reason_code у отдельных заявок, не в сводке.
2. Гоняем стадию Б для каждой заявки, у которой есть снимок стадии Б —
   независимо от того, что именно поменялось в конфиге. Если заявка была
   отклонена на стадии А (по любой из двух версий), а снимка стадии Б для
   неё нет — дальнейший исход неизвестен, это отдельная категория, а не
   "изменилось"/"не изменилось".
3. Гейт "только после симуляции" и просрочка симуляции — в app/api/rulesets.py
   (там же, где сама запись RulesetSimulation), не здесь: это чистая функция
   входа-выхода, без знания о HTTP/БД-персистентности результата.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.rules import evaluate_financial, evaluate_hard_stops, evaluate_rule_list, evaluate_stage_b_pre_bki
from app.engine.schema import RulesetConfig
from app.models import Application, Client, Decision, FeatureSnapshot

# Категории исхода заявки под одной версией конфига.
OUTCOME_REJECTED_A = "rejected_a"
OUTCOME_REJECTED_B = "rejected_b"
OUTCOME_MANUAL_REVIEW = "manual_review"
OUTCOME_APPROVED = "approved"
# Стадия Б не пройдена (нет снимка) — с ЭТОЙ стороны сравнения исход неизвестен.
OUTCOME_NO_DATA = "no_data"

_KNOWN_OUTCOMES = (OUTCOME_REJECTED_A, OUTCOME_REJECTED_B, OUTCOME_MANUAL_REVIEW, OUTCOME_APPROVED)

# Сколько application_id класть в детальный список на категорию/правило —
# сводка не должна раздуваться до размера выборки целиком (ваше "N заявок",
# не поимённо все).
_MAX_LISTED_IDS = 50


@dataclass(frozen=True)
class _SideResult:
    outcome: str
    reason_code: str | None


def _evaluate_side(config: RulesetConfig, features_a: dict[str, Any] | None, features_b: dict[str, Any] | None) -> _SideResult:
    if features_a is None:
        return _SideResult(OUTCOME_NO_DATA, None)

    stage_a = evaluate_hard_stops(config, features_a)
    if stage_a.matched:
        return _SideResult(OUTCOME_REJECTED_A, stage_a.reason_code)

    if features_b is None:
        return _SideResult(OUTCOME_NO_DATA, None)

    pre_bki = evaluate_stage_b_pre_bki(config, features_b)
    if pre_bki.matched:
        return _SideResult(OUTCOME_REJECTED_B, pre_bki.reason_code)

    financial = evaluate_financial(config, features_b)
    outcome = {"approve": OUTCOME_APPROVED, "reject": OUTCOME_REJECTED_B, "manual_review": OUTCOME_MANUAL_REVIEW}[
        financial.outcome
    ]
    return _SideResult(outcome, financial.reason_code)


def _not_computable_rule_ids(config: RulesetConfig, features_a: dict | None, features_b: dict | None) -> set[str]:
    """Правила КАНДИДАТА, которые реально дошли бы до оценки для этой заявки
    (по такому же короткому замыканию между стадиями, что и _evaluate_side),
    но нужное им поле отсутствует в снимке — «не вычислимо», не «не сработало»."""
    if features_a is None:
        return set()

    not_computable: set[str] = set()
    _, missing_a = evaluate_rule_list(config.hard_stops, features_a)
    not_computable |= {r.id for r in config.hard_stops if r.field in missing_a}

    stage_a = evaluate_hard_stops(config, features_a)
    if stage_a.matched or features_b is None:
        return not_computable

    _, missing_pre_bki = evaluate_rule_list(config.stage_b_pre_bki, features_b)
    not_computable |= {r.id for r in config.stage_b_pre_bki if r.field in missing_pre_bki}

    pre_bki = evaluate_stage_b_pre_bki(config, features_b)
    if pre_bki.matched:
        return not_computable

    _, missing_soft = evaluate_rule_list(config.soft_rules, features_b)
    not_computable |= {r.id for r in config.soft_rules if r.field in missing_soft}
    return not_computable


@dataclass
class ApplicationSimulationRow:
    application_id: UUID
    external_id: str
    client_name: str | None  # None если клиент ещё не привязан ИЛИ роль без права на ПДн (решает вызывающий код)
    active_outcome: str
    active_reason_code: str | None
    candidate_outcome: str
    candidate_reason_code: str | None
    actual_decision_outcome: str | None  # справочно — не участвует в сводке (уточнение 1)
    actual_decision_reason_code: str | None


@dataclass
class SimulationSummary:
    sample_size_requested: int
    sample_size_actual: int
    changed_count: int
    approval_rate_active: float | None
    approval_rate_candidate: float | None
    known_denominator: int  # заявок, где обе версии дают известный исход — база для approval_rate/*
    swap_in: list[ApplicationSimulationRow]  # раньше не approved, кандидат -> approved
    swap_out: list[ApplicationSimulationRow]  # раньше approved, кандидат -> не approved
    unknown_after_stage_a_pass: list[ApplicationSimulationRow]  # уточнение 2: особая категория
    unknown_after_stage_a_pass_total: int
    insufficient_data_count: int  # обе стороны no_data — заявка ещё не дошла до стадии Б в реальности
    not_computable: list[dict[str, Any]]  # [{rule_id, count, application_ids: [...]}]
    defaultness_by_known_outcome: dict[str, Any]


async def _latest_feature_snapshots(
    session: AsyncSession, application_ids: list[UUID]
) -> dict[tuple[UUID, str], dict[str, Any]]:
    if not application_ids:
        return {}
    rows = (
        await session.execute(
            select(FeatureSnapshot)
            .where(FeatureSnapshot.application_id.in_(application_ids))
            .order_by(FeatureSnapshot.application_id, FeatureSnapshot.stage, FeatureSnapshot.computed_at.desc())
            .distinct(FeatureSnapshot.application_id, FeatureSnapshot.stage)
        )
    ).scalars().all()
    return {(row.application_id, row.stage): row.features for row in rows}


async def _latest_decisions(
    session: AsyncSession, application_ids: list[UUID]
) -> dict[tuple[UUID, str], Decision]:
    if not application_ids:
        return {}
    rows = (
        await session.execute(
            select(Decision)
            .where(Decision.application_id.in_(application_ids))
            .order_by(Decision.application_id, Decision.stage, Decision.created_at.desc())
            .distinct(Decision.application_id, Decision.stage)
        )
    ).scalars().all()
    return {(row.application_id, row.stage): row for row in rows}


async def _known_bad_by_application(session: AsyncSession, application_ids: list[UUID]) -> dict[UUID, int]:
    """T15: application_id -> bad (0/1) из target_bad_60_6, ТОЛЬКО для
    зрелых договоров (is_mature) — незрелые/непривязанные заявки просто не
    попадают в этот словарь, не считаются ни хорошими, ни плохими (ТЗ, T15
    п.5: таргет известен — или не используется вовсе, не 0 по умолчанию)."""
    if not application_ids:
        return {}
    rows = (
        await session.execute(
            text(
                """
                SELECT c.application_id, t.bad
                FROM contracts c
                JOIN target_bad_60_6 t ON t.contract_id = c.id
                WHERE c.application_id = ANY(:application_ids) AND t.is_mature
                """
            ),
            {"application_ids": list(application_ids)},
        )
    ).all()
    return {row.application_id: row.bad for row in rows}


async def run_simulation(
    session: AsyncSession, *, active_config: RulesetConfig, candidate_config: RulesetConfig, sample_size: int
) -> SimulationSummary:
    """Только чтение — ни одна из выборок и функций здесь не пишет в БД."""
    applications = (
        await session.execute(
            select(Application.id, Application.external_id, Application.client_id)
            .order_by(Application.submitted_at.desc())
            .limit(sample_size)
        )
    ).all()
    app_ids = [row.id for row in applications]

    snapshots = await _latest_feature_snapshots(session, app_ids)
    known_bad = await _known_bad_by_application(session, app_ids)
    decisions = await _latest_decisions(session, app_ids)

    client_ids = {row.client_id for row in applications if row.client_id is not None}
    clients: dict[UUID, Client] = {}
    if client_ids:
        client_rows = (await session.execute(select(Client).where(Client.id.in_(client_ids)))).scalars().all()
        clients = {c.id: c for c in client_rows}

    changed_count = 0
    known_denominator = 0
    active_approved = 0
    candidate_approved = 0
    # T15: дефолтность по известным исходам — {outcome: [n_known, n_bad]}, отдельно
    # для активной версии и кандидата (одна и та же заявка может попасть в разные
    # outcome-категории по разным конфигам).
    default_by_active_outcome: dict[str, list[int]] = {}
    default_by_candidate_outcome: dict[str, list[int]] = {}
    swap_in: list[ApplicationSimulationRow] = []
    swap_out: list[ApplicationSimulationRow] = []
    unknown_after_stage_a_pass: list[ApplicationSimulationRow] = []
    unknown_after_stage_a_pass_total = 0
    insufficient_data_count = 0
    not_computable_ids: dict[str, set[UUID]] = {}

    for row in applications:
        features_a = snapshots.get((row.id, "A"))
        features_b = snapshots.get((row.id, "B"))

        active = _evaluate_side(active_config, features_a, features_b)
        candidate = _evaluate_side(candidate_config, features_a, features_b)

        for rule_id in _not_computable_rule_ids(candidate_config, features_a, features_b):
            not_computable_ids.setdefault(rule_id, set()).add(row.id)

        if active.outcome == OUTCOME_REJECTED_A and candidate.outcome == OUTCOME_NO_DATA:
            unknown_after_stage_a_pass_total += 1
            if len(unknown_after_stage_a_pass) < _MAX_LISTED_IDS:
                unknown_after_stage_a_pass.append(
                    _build_row(row, active, candidate, decisions, clients)
                )
            continue

        if active.outcome == OUTCOME_NO_DATA and candidate.outcome == OUTCOME_NO_DATA:
            insufficient_data_count += 1
            continue

        known_denominator += 1
        if active.outcome == OUTCOME_APPROVED:
            active_approved += 1
        if candidate.outcome == OUTCOME_APPROVED:
            candidate_approved += 1

        bad = known_bad.get(row.id)
        if bad is not None:
            bucket = default_by_active_outcome.setdefault(active.outcome, [0, 0])
            bucket[0] += 1
            bucket[1] += bad
            bucket = default_by_candidate_outcome.setdefault(candidate.outcome, [0, 0])
            bucket[0] += 1
            bucket[1] += bad

        if active.outcome != candidate.outcome:
            changed_count += 1
            if active.outcome != OUTCOME_APPROVED and candidate.outcome == OUTCOME_APPROVED:
                if len(swap_in) < _MAX_LISTED_IDS:
                    swap_in.append(_build_row(row, active, candidate, decisions, clients))
            elif active.outcome == OUTCOME_APPROVED and candidate.outcome != OUTCOME_APPROVED:
                if len(swap_out) < _MAX_LISTED_IDS:
                    swap_out.append(_build_row(row, active, candidate, decisions, clients))

    approval_rate_active = active_approved / known_denominator if known_denominator else None
    approval_rate_candidate = candidate_approved / known_denominator if known_denominator else None

    not_computable = [
        {"rule_id": rule_id, "count": len(ids), "application_ids": sorted(str(i) for i in ids)[:_MAX_LISTED_IDS]}
        for rule_id, ids in sorted(not_computable_ids.items())
    ]

    if default_by_active_outcome or default_by_candidate_outcome:
        defaultness_by_known_outcome: dict[str, Any] = {
            "available": True,
            "by_active_outcome": {
                outcome: {"known": n, "bad": bad, "bad_rate": bad / n}
                for outcome, (n, bad) in sorted(default_by_active_outcome.items())
            },
            "by_candidate_outcome": {
                outcome: {"known": n, "bad": bad, "bad_rate": bad / n}
                for outcome, (n, bad) in sorted(default_by_candidate_outcome.items())
            },
        }
    else:
        defaultness_by_known_outcome = {
            "available": False,
            "note": "Появится после импорта платежей и целевой переменной (T15) — "
            "в выборке нет ни одного зрелого договора с известным исходом.",
        }

    return SimulationSummary(
        sample_size_requested=sample_size,
        sample_size_actual=len(applications),
        changed_count=changed_count,
        approval_rate_active=approval_rate_active,
        approval_rate_candidate=approval_rate_candidate,
        known_denominator=known_denominator,
        swap_in=swap_in,
        swap_out=swap_out,
        unknown_after_stage_a_pass=unknown_after_stage_a_pass,
        unknown_after_stage_a_pass_total=unknown_after_stage_a_pass_total,
        insufficient_data_count=insufficient_data_count,
        not_computable=not_computable,
        defaultness_by_known_outcome=defaultness_by_known_outcome,
    )


def _build_row(row, active: _SideResult, candidate: _SideResult, decisions: dict, clients: dict) -> ApplicationSimulationRow:
    client = clients.get(row.client_id) if row.client_id else None
    client_name = f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip() if client else None

    actual_decision = decisions.get((row.id, "B")) or decisions.get((row.id, "A"))

    return ApplicationSimulationRow(
        application_id=row.id,
        external_id=row.external_id,
        client_name=client_name,
        active_outcome=active.outcome,
        active_reason_code=active.reason_code,
        candidate_outcome=candidate.outcome,
        candidate_reason_code=candidate.reason_code,
        actual_decision_outcome=actual_decision.outcome if actual_decision else None,
        actual_decision_reason_code=actual_decision.reason_code if actual_decision else None,
    )
