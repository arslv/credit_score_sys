"""Федресурс, банкротства (T09, ТЗ «Федресурс»). Публичного API нет — только
парсинг внутреннего эндпоинта, которым пользуется сам сайт. Ни точный адрес,
ни формат ответа этого эндпоинта не подтверждены: сайт недоступен из окружения
разработки (геоблокировка), а угадывать адрес запрещено системными инструкциями
проекта. Реализация парсинга заняла бы больше времени, чем ФССП+ФНС вместе (по
факту — вообще невозможна без подтверждённого адреса), поэтому по плану T09
здесь заглушка: FedresursHttpSource всегда source_unavailable. Заявка не
теряется — флаг оператору (ТЗ, «Поведение при отказе источника»), сама
проверка банкротств почти всегда дублируется в отчёте БКИ на стадии Б."""

from app.sources.base import CheckResult, CheckStatus, Subject


class FakeFedresursSource:
    """hits: множество ключей "Фамилия|Имя|ГГГГ-ММ-ДД" — детерминированный фейк."""

    source_name = "fedresurs"

    def __init__(self, *, hits: set[str] | None = None, raise_error: bool = False) -> None:
        self._hits = hits or set()
        self._raise_error = raise_error

    async def check(self, subject: Subject) -> CheckResult:
        if not subject.last_name or not subject.first_name or not subject.birth_date:
            return CheckResult(status=CheckStatus.NO_DATA)
        if self._raise_error:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="fake fedresurs unavailable")

        key = f"{subject.last_name}|{subject.first_name}|{subject.birth_date.isoformat()}"
        if key in self._hits:
            return CheckResult(status=CheckStatus.HIT, parsed={"bankrupt": True})
        return CheckResult(status=CheckStatus.OK)


class FedresursHttpSource:
    """Заглушка (T09) — см. докстринг модуля. Всегда source_unavailable."""

    source_name = "fedresurs"

    async def check(self, subject: Subject) -> CheckResult:
        return CheckResult(
            status=CheckStatus.SOURCE_UNAVAILABLE,
            error_text="Федресурс: внутренний эндпоинт не подтверждён, см. app/sources/fedresurs.py",
        )
