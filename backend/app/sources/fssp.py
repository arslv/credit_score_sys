"""ФССП, исполнительные производства (T09, ТЗ «ФССП»). Официальное API
асинхронное: запрос -> ID -> опрос статуса отдельными вызовами — не укладывается
в однократный SourceBase.check() (app/sources/base.py), поэтому отдельный
протокол FsspSource с submit()/poll(). Дата рождения обязательна (ТЗ) — без неё
пустой результат уже на этапе submit, вызывать источник незачем.

Ключа доступа к api-ip.fssp.gov.ru у проекта нет (регистрация — отдельно,
человеком, вне рамок этой задачи, см. docs/TASKS.md T09 «Вне рамок»), поэтому
FsspHttpSource ниже собран по документированному формату API, но вживую не
проверен."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.sources.base import CheckResult, CheckStatus, Subject


class FsspSubmitError(Exception):
    """ФССП не принял запрос (сбой/таймаут на submit). Единственное место в
    проекте, где источник намеренно бросает исключение наружу вместо
    CheckResult — потому что submit() не часть контракта SourceBase.check()
    (правило 4 CLAUDE.md про него), а вызывающий пайплайн сам решает: сразу
    source_unavailable, без ожидания дедлайна опроса (T09, п.4 поправок)."""


class FsspSource(Protocol):
    source_name: str

    async def submit(self, subject: Subject) -> str:
        """Возвращает external_request_id. Бросает FsspSubmitError при сбое."""
        ...

    async def poll(self, request_id: str) -> CheckResult | None:
        """None — результат ещё не готов, опрашивать дальше. Само по себе
        никогда не бросает исключение (как и SourceBase.check())."""
        ...


class FakeFsspSource:
    """Для тестов: submit сразу выдаёт request_id (или бросает FsspSubmitError
    при raise_on_submit); poll возвращает None `not_ready_polls` раз подряд на
    каждый request_id (или всегда None при never_ready — тест дедлайна опроса),
    затем — результат из productions_by_request (по умолчанию пусто -> ok)."""

    source_name = "fssp"

    def __init__(
        self,
        *,
        productions_by_request: dict[str, list[dict[str, Any]]] | None = None,
        not_ready_polls: int = 0,
        raise_on_submit: bool = False,
        never_ready: bool = False,
    ) -> None:
        self._productions = productions_by_request or {}
        self._not_ready_polls = not_ready_polls
        self._raise_on_submit = raise_on_submit
        self._never_ready = never_ready
        self._poll_counts: dict[str, int] = {}
        self.submitted: list[Subject] = []

    async def submit(self, subject: Subject) -> str:
        if not subject.last_name or not subject.first_name or not subject.birth_date:
            raise FsspSubmitError("нет обязательных данных (ФИО + дата рождения)")
        if self._raise_on_submit:
            raise FsspSubmitError("fake fssp submit failed")
        self.submitted.append(subject)
        return f"fake-{len(self.submitted)}"

    async def poll(self, request_id: str) -> CheckResult | None:
        if self._never_ready:
            return None
        count = self._poll_counts.get(request_id, 0) + 1
        self._poll_counts[request_id] = count
        if count <= self._not_ready_polls:
            return None

        productions = self._productions.get(request_id, [])
        if not productions:
            return CheckResult(status=CheckStatus.OK, parsed={"productions": []})
        return CheckResult(status=CheckStatus.HIT, parsed={"productions": productions})


@dataclass(frozen=True)
class FsspHttpSource:
    """Не проверялся вживую — нет ключа доступа (см. докстринг модуля)."""

    base_url: str
    api_key: str
    submit_timeout_seconds: float
    submit_max_attempts: int

    source_name: str = "fssp"

    async def submit(self, subject: Subject) -> str:
        if not subject.last_name or not subject.first_name or not subject.birth_date:
            raise FsspSubmitError("нет обязательных данных (ФИО + дата рождения)")
        if not self.base_url or not self.api_key:
            raise FsspSubmitError("FSSP_BASE_URL/FSSP_API_KEY не заданы")

        request = {
            "last_name": subject.last_name,
            "first_name": subject.first_name,
            "middle_name": subject.middle_name,
            "birth_date": subject.birth_date.isoformat(),
        }
        last_error: Exception | None = None
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.submit_timeout_seconds) as client:
            for _ in range(self.submit_max_attempts):
                try:
                    response = await client.post(
                        "/api/v1/requests", json=request, headers={"Authorization": f"Bearer {self.api_key}"}
                    )
                    response.raise_for_status()
                    payload = response.json()
                    return payload["request_id"]
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    continue
        raise FsspSubmitError(str(last_error))

    async def poll(self, request_id: str) -> CheckResult | None:
        request = {"request_id": request_id}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.submit_timeout_seconds) as client:
                response = await client.get(
                    f"/api/v1/requests/{request_id}", headers={"Authorization": f"Bearer {self.api_key}"}
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001 — poll(), как и check(), не бросает наружу
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, request=request, error_text=str(exc))

        if payload.get("status") != "ready":
            return None

        productions = payload.get("productions", [])
        status = CheckStatus.HIT if productions else CheckStatus.OK
        return CheckResult(status=status, request=request, response_raw=payload, parsed={"productions": productions})
