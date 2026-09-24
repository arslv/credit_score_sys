"""ФНС, получение ИНН (T09, ТЗ «ФНС»). Сервис «Узнать ИНН» официально не
документирован, формат менялся (ТЗ) — HTTP-клиент ниже собран по общедоступному
описанию запроса/ответа, вживую не проверялся (геоблокировка окружения
разработки — правило проекта: никаких живых запросов к госсайтам при разработке).
Включается явно (external_sources_mode=http), проверяется, когда появится доступ."""

import httpx

from app.sources.base import CheckResult, CheckStatus, Subject


class FakeFnsSource:
    """responses: {"Фамилия|Имя|ГГГГ-ММ-ДД": "ИНН"} — детерминированный фейк для тестов."""

    source_name = "fns"

    def __init__(self, *, responses: dict[str, str] | None = None, raise_error: bool = False) -> None:
        self._responses = responses or {}
        self._raise_error = raise_error

    async def check(self, subject: Subject) -> CheckResult:
        if not subject.last_name or not subject.first_name or not subject.birth_date:
            return CheckResult(status=CheckStatus.NO_DATA)
        if self._raise_error:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="fake fns unavailable")

        key = f"{subject.last_name}|{subject.first_name}|{subject.birth_date.isoformat()}"
        inn = self._responses.get(key)
        if inn is None:
            return CheckResult(status=CheckStatus.NO_DATA)
        return CheckResult(status=CheckStatus.OK, parsed={"inn": inn})


class FnsHttpSource:
    source_name = "fns"

    def __init__(self, *, base_url: str, timeout_seconds: float, max_attempts: int) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts

    async def check(self, subject: Subject) -> CheckResult:
        if not subject.last_name or not subject.first_name or not subject.birth_date:
            return CheckResult(status=CheckStatus.NO_DATA)
        if not self._base_url:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="FNS_BASE_URL не задан")

        request = {
            "last_name": subject.last_name,
            "first_name": subject.first_name,
            "middle_name": subject.middle_name,
            "birth_date": subject.birth_date.isoformat(),
            "passport_series": subject.passport_series,
            "passport_number": subject.passport_number,
        }

        last_error: Exception | None = None
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout_seconds) as client:
            for _ in range(self._max_attempts):
                try:
                    response = await client.post("/", data=request)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001 — источник никогда не бросает наружу (правило 4)
                    last_error = exc
                    continue

                inn = payload.get("inn")
                if not inn:
                    return CheckResult(status=CheckStatus.NO_DATA, request=request, response_raw=payload)
                return CheckResult(
                    status=CheckStatus.OK, request=request, response_raw=payload, parsed={"inn": inn}
                )

        return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, request=request, error_text=str(last_error))
