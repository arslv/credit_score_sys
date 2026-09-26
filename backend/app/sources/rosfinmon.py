"""Росфинмониторинг: перечни Т/Э, ФРОМУ, МВК, ООН — локальная копия, нечёткий поиск
по ФИО через pg_trgm + сверка даты рождения (ТЗ, «Особенности сверки по перечням»)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SanctionsList
from app.sources.base import CheckResult, CheckStatus, Subject
from app.sources.name_normalize import full_name, normalize_name, sanctions_entry_key


class RosfinmonSource:
    source_name = "rosfinmon"

    def __init__(self, session: AsyncSession, *, name_match_min_similarity: float) -> None:
        self._session = session
        self._threshold = name_match_min_similarity

    async def check(self, subject: Subject) -> CheckResult:
        if not subject.last_name or not subject.first_name:
            return CheckResult(status=CheckStatus.NO_DATA)

        query_name = normalize_name(full_name(subject.last_name, subject.first_name, subject.middle_name))
        request = {"name_normalized": query_name, "birth_date": str(subject.birth_date) if subject.birth_date else None}

        similarity = func.similarity(SanctionsList.name_normalized, query_name)
        stmt = (
            select(SanctionsList, similarity.label("sim"))
            .where(similarity >= self._threshold)
            .order_by(similarity.desc())
            .limit(5)
        )
        try:
            rows = (await self._session.execute(stmt)).all()
        except Exception as exc:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, request=request, error_text=str(exc))

        # Дата рождения — дополнительная сверка: без совпадения по ФИО+ДР перечень
        # слишком шумный (см. ТЗ про Дагестан и массовые ложные совпадения).
        matches = [
            (entry, sim)
            for entry, sim in rows
            if subject.birth_date is None or entry.birth_date is None or entry.birth_date == subject.birth_date
        ]

        if not matches:
            return CheckResult(status=CheckStatus.OK, request=request)

        best_entry, best_sim = matches[0]
        return CheckResult(
            status=CheckStatus.HIT,
            request=request,
            parsed={
                # sanctions_list_id — id СТРОКИ на момент ЭТОЙ проверки, только
                # для отладки/трассировки: реестр перезаливается целиком, при
                # следующей синхронизации даже неизменная запись получит новый id.
                "sanctions_list_id": str(best_entry.id),
                # sanctions_entry_key — устойчивый признак записи, переживающий
                # перезаливку реестра; ключ подавления ложных срабатываний
                # (T12, app/engine/compliance_overrides.py) — пара клиент+запись.
                "sanctions_entry_key": sanctions_entry_key(
                    official_id=(best_entry.extra or {}).get("official_id") if best_entry.extra else None,
                    name_normalized=best_entry.name_normalized,
                    birth_date=best_entry.birth_date,
                ),
                "list_type": best_entry.list_type,
                "full_name": best_entry.full_name,
                "similarity": round(float(best_sim), 3),
                "birth_date_matched": best_entry.birth_date == subject.birth_date
                if subject.birth_date and best_entry.birth_date
                else None,
            },
        )
