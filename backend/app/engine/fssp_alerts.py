"""Действующие пометки «ФССП нашёл долг после решения».

Пометка (fssp_post_decision_alerts, append-only) снимается сама, без удаления
строк, когда она больше не актуальна:
- ФССП по тому же человеку проверили ещё раз (более поздняя проверка): если
  долг снова найден, у новой проверки своя пометка; если нет — пересматривать
  нечего;
- по заявке принято новое решение — пересмотр состоялся."""

from sqlalchemy import and_, exists, not_, select
from sqlalchemy.orm import aliased

from app.models import Check, Decision, FsspPostDecisionAlert


def active_alert_condition():
    alert_check = aliased(Check)
    newer_check = aliased(Check)
    newer_fssp_check = exists(
        select(newer_check.id)
        .select_from(alert_check)
        .where(
            alert_check.id == FsspPostDecisionAlert.check_id,
            newer_check.application_id == FsspPostDecisionAlert.application_id,
            newer_check.source == "fssp",
            newer_check.subject_type == FsspPostDecisionAlert.subject_type,
            newer_check.subject_id.is_not_distinct_from(FsspPostDecisionAlert.subject_id),
            newer_check.started_at > alert_check.started_at,
        )
    )
    newer_decision = exists(
        select(Decision.id).where(
            Decision.application_id == FsspPostDecisionAlert.application_id,
            Decision.created_at > FsspPostDecisionAlert.created_at,
        )
    )
    return and_(not_(newer_fssp_check), not_(newer_decision))
