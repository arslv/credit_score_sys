"""ФНС (T09, переписано в T16 по итогам живой разведки — ТЗ «ФНС»): три
независимых источника на разных доменах одной платформы ("uni", общий стек
статики nalog.ru):

- fns_inn — ИНН по ФИО+дате рождения+паспорту, service.nalog.ru/inn.do.
  Согласие на обработку ПД — обязательный первый шаг (страница, кука), без
  него /inn.do всегда редиректит на неё же; это не обход, мы проходим его как
  обычный браузер. Дальше — асинхронный поиск: POST inn-new-proc.json
  (c=find) -> requestId -> поллинг (c=get) до готовности.
- fns_npd — статус плательщика НПД (самозанятость) по ИНН,
  statusnpd.nalog.ru, простой синхронный POST, без согласия и капчи.
- fns_egrip — зарегистрирован ли как ИП, по ИНН, egrul.nalog.ru (та же
  платформа, что ЕГРЮЛ). Без согласия. POST на корень "/" (НЕ "/index.html" —
  там 405, статика) -> {t, captchaRequired} -> поллинг search-result/<t>.

Капча — рейт-лимит-триггерная (не всегда присутствует), у всех трёх доменов
платформы "uni" потенциально есть (встречается форма с полями
captcha/captchaToken/token1/token2). Капчу НИКОГДА не решаем (ваше жёсткое
правило — капчу решает только человек, здесь — вообще никто: при
captchaRequired=true просто откладываем попытку и повторяем позже, не
показываем оператору). После исчерпания попыток — SOURCE_UNAVAILABLE
(правило 4 CLAUDE.md), без автоотказа.

Частота запросов — через app.sources.throttle.throttle (Redis), отдельно для
fns_inn+fns_egrip (одна платформа — одна общая пауза) и для fns_npd (свой
задокументированный лимит 2/мин, ваше указание из разведки)."""

import asyncio
import time
from datetime import date
from typing import Any
from urllib.parse import urljoin

import httpx
from redis.asyncio import Redis

from app.sources.base import CheckResult, CheckStatus, Subject, missing_subject_field_error
from app.sources.throttle import throttle

_FNS_SHARED_THROTTLE_KEY = "throttle:fns:shared"  # inn + egrip, одна платформа
_FNS_NPD_THROTTLE_KEY = "throttle:fns:npd"

_UA = "Mozilla/5.0 (compatible; DialogScoring/1.0)"


def _format_docno(series: str, number: str) -> str:
    """"СС СС ННННННН" — маска реального поля (разведка T16, inn-new.js,
    doctype=21 паспорт гражданина РФ): серия (4 цифры, с пробелом внутри) +
    номер (6-7 цифр)."""
    series_digits = "".join(ch for ch in series if ch.isdigit())
    number_digits = "".join(ch for ch in number if ch.isdigit())
    if len(series_digits) != 4:
        return f"{series} {number}"
    return f"{series_digits[:2]} {series_digits[2:]} {number_digits}"


FNS_INN_REQUIRED_FIELDS = (
    "last_name", "first_name", "birth_date", "passport_series", "passport_number", "passport_issue_date",
)


class FakeFnsInnSource:
    """Для тестов и e2e_check.py: responses — {"Фамилия|Имя|ГГГГ-ММ-ДД": "ИНН"}."""

    source_name = "fns_inn"

    def __init__(self, *, responses: dict[str, str] | None = None, raise_unavailable: bool = False) -> None:
        self._responses = responses or {}
        self._raise_unavailable = raise_unavailable
        self.check_calls: list[Subject] = []

    async def check(self, subject: Subject) -> CheckResult:
        self.check_calls.append(subject)
        missing = missing_subject_field_error(subject, FNS_INN_REQUIRED_FIELDS)
        if missing:
            return CheckResult(status=CheckStatus.ERROR, error_text=missing)
        if self._raise_unavailable:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="fake fns_inn unavailable")
        key = f"{subject.last_name}|{subject.first_name}|{subject.birth_date.isoformat()}"
        inn = self._responses.get(key)
        if inn is None:
            return CheckResult(status=CheckStatus.NO_DATA)
        return CheckResult(status=CheckStatus.OK, parsed={"inn": inn, "found": True})


class FakeFnsNpdSource:
    """responses — {"ИНН": bool (самозанятый или нет)}."""

    source_name = "fns_npd"

    def __init__(self, *, responses: dict[str, bool] | None = None, raise_unavailable: bool = False) -> None:
        self._responses = responses or {}
        self._raise_unavailable = raise_unavailable

    async def check_by_inn(self, inn: str) -> CheckResult:
        if self._raise_unavailable:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="fake fns_npd unavailable")
        is_self_employed = self._responses.get(inn)
        if is_self_employed is None:
            return CheckResult(status=CheckStatus.NO_DATA, parsed={"inn": inn})
        status = CheckStatus.OK if is_self_employed else CheckStatus.NO_DATA
        return CheckResult(status=status, parsed={"inn": inn, "is_self_employed": is_self_employed})


class FakeFnsEgripSource:
    """responses — {"ИНН": True} для зарегистрированных как ИП."""

    source_name = "fns_egrip"

    def __init__(self, *, responses: dict[str, bool] | None = None, raise_unavailable: bool = False) -> None:
        self._responses = responses or {}
        self._raise_unavailable = raise_unavailable

    async def check_by_inn(self, inn: str) -> CheckResult:
        if self._raise_unavailable:
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text="fake fns_egrip unavailable")
        is_ip = bool(self._responses.get(inn))
        status = CheckStatus.OK if is_ip else CheckStatus.NO_DATA
        return CheckResult(status=status, parsed={"inn": inn, "is_individual_entrepreneur": is_ip})


class FnsInnHttpSource:
    source_name = "fns_inn"

    def __init__(
        self, *, redis: Redis, request_timeout_seconds: float, max_attempts: int,
        retry_backoff_seconds: float, throttle_interval_seconds: float,
    ) -> None:
        self._redis = redis
        self._timeout = request_timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = retry_backoff_seconds
        self._throttle_interval = throttle_interval_seconds

    async def check(self, subject: Subject) -> CheckResult:
        missing = missing_subject_field_error(subject, FNS_INN_REQUIRED_FIELDS)
        if missing:
            return CheckResult(status=CheckStatus.ERROR, error_text=missing)

        last_error: str | None = None
        for attempt in range(self._max_attempts):
            await throttle(self._redis, _FNS_SHARED_THROTTLE_KEY, self._throttle_interval)
            try:
                async with httpx.AsyncClient(
                    base_url="https://service.nalog.ru", timeout=self._timeout, headers={"User-Agent": _UA},
                ) as client:
                    await self._ensure_consent(client)
                    request_body = {
                        "c": "find",
                        "fam": subject.last_name,
                        "nam": subject.first_name,
                        "otch": subject.middle_name or "",
                        "bdate": subject.birth_date.strftime("%d.%m.%Y"),
                        "doctype": "21",
                        "docno": _format_docno(subject.passport_series, subject.passport_number),
                        "docdt": subject.passport_issue_date.strftime("%d.%m.%Y"),
                    }
                    response = await client.post("/inn-new-proc.json", data=request_body)
                    response.raise_for_status()
                    payload = response.json()

                    if payload.get("captchaRequired"):
                        last_error = "captchaRequired=true (рейт-лимит ФНС)"
                        await asyncio.sleep(self._backoff)  # шире, чем общий throttle — мы уже разогнались
                        continue  # не решаем капчу — просто ждём следующей попытки

                    request_id = payload["requestId"]
                    result = await self._poll(client, request_id)
            except Exception as exc:  # noqa: BLE001 — источник никогда не бросает наружу (правило 4)
                last_error = str(exc)
                continue

            if result is None:
                last_error = "сервис не вернул результат за отведённое время"
                continue
            return result

        return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text=last_error)

    async def _ensure_consent(self, client: httpx.AsyncClient) -> None:
        """GET /inn.do: либо сразу форма (согласие уже было в этой httpx-сессии
        — не бывает на свежем клиенте, но проверяем), либо редирект на
        /static/personal-data.html — проходим его (ваше уточнение: это не
        обход, обычный шаг браузера) и постим согласие на URL, посчитанный
        ОТНОСИТЕЛЬНО страницы согласия (не от корня — разведка T16: это и был
        мой первый баг, POST на /personal-data-proc.json молча уводил не туда)."""
        probe = await client.get("/inn.do", follow_redirects=False)
        if probe.status_code != 302:
            return
        consent_page_url = urljoin(str(probe.url), probe.headers["location"])
        consent_page = await client.get(consent_page_url)
        consent_page.raise_for_status()
        consent_proc_url = urljoin(str(consent_page.url), "personal-data-proc.json")
        consent_response = await client.post(
            consent_proc_url, data={"from": "/inn.do", "svc": "inn", "personalData": "1"},
        )
        consent_response.raise_for_status()

    async def _poll(self, client: httpx.AsyncClient, request_id: str) -> CheckResult | None:
        """Тот же ритм, что у сайта (inn-new.js::getCheckTimeout): растущая
        пауза 1с/2с/.../до 60с, пока не кончится общий таймаут запроса."""

        delay = 0.0
        elapsed = 0.0
        while elapsed < self._timeout:
            if delay:
                await asyncio.sleep(delay)
                elapsed += delay
            delay = min(delay + 1.0, 60.0) if delay else 1.0

            response = await client.post("/inn-new-proc.json", data={"c": "get", "requestId": request_id})
            response.raise_for_status()
            payload = response.json()
            state = payload.get("state")

            if state == 1:
                return CheckResult(
                    status=CheckStatus.OK, response_raw=payload,
                    parsed={"inn": payload["inn"], "found": True},
                )
            if state == 0:
                return CheckResult(status=CheckStatus.NO_DATA, response_raw=payload, parsed={"found": False})
            if state == -1:
                continue
            # -2 (сервис недоступен) или неожиданный код — наружу как обычный SOURCE_UNAVAILABLE
            return CheckResult(
                status=CheckStatus.SOURCE_UNAVAILABLE, response_raw=payload,
                error_text=f"state={state}",
            )
        return None


class FnsNpdHttpSource:
    source_name = "fns_npd"

    def __init__(
        self, *, redis: Redis, request_timeout_seconds: float, throttle_interval_seconds: float,
    ) -> None:
        self._redis = redis
        self._timeout = request_timeout_seconds
        self._throttle_interval = throttle_interval_seconds

    async def check_by_inn(self, inn: str, *, as_of: date) -> CheckResult:
        await throttle(self._redis, _FNS_NPD_THROTTLE_KEY, self._throttle_interval)
        request = {"inn": inn, "requestDate": as_of.isoformat()}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _UA}) as client:
                response = await client.post(
                    "https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status", json=request,
                )
                if response.status_code != 200:
                    return CheckResult(
                        status=CheckStatus.SOURCE_UNAVAILABLE, request=request,
                        error_text=f"HTTP {response.status_code}: {response.text[:200]}",
                    )
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, request=request, error_text=str(exc))

        is_self_employed = bool(payload.get("status"))
        status = CheckStatus.OK if is_self_employed else CheckStatus.NO_DATA
        return CheckResult(
            status=status, request=request, response_raw=payload,
            parsed={"inn": inn, "is_self_employed": is_self_employed},
        )


class FnsEgripHttpSource:
    source_name = "fns_egrip"

    def __init__(
        self, *, redis: Redis, request_timeout_seconds: float, max_attempts: int,
        retry_backoff_seconds: float, throttle_interval_seconds: float,
    ) -> None:
        self._redis = redis
        self._timeout = request_timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = retry_backoff_seconds
        self._throttle_interval = throttle_interval_seconds

    async def check_by_inn(self, inn: str) -> CheckResult:
        last_error: str | None = None
        for _attempt in range(self._max_attempts):
            await throttle(self._redis, _FNS_SHARED_THROTTLE_KEY, self._throttle_interval)
            try:
                async with httpx.AsyncClient(
                    base_url="https://egrul.nalog.ru", timeout=self._timeout, headers={"User-Agent": _UA},
                ) as client:
                    # POST на корень "/" — НЕ "/index.html" (разведка T16: там
                    # отдаётся как статика, POST туда -> 405).
                    submit = await client.post("/", data={"query": inn, "region": "", "page": "", "vyp3CaptchaToken": ""})
                    submit.raise_for_status()
                    submit_payload = submit.json()

                    if submit_payload.get("captchaRequired"):
                        last_error = "captchaRequired=true (рейт-лимит ФНС)"
                        await asyncio.sleep(self._backoff)
                        continue

                    token = submit_payload["t"]
                    result = await self._poll(client, token, inn)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                continue

            if result is None:
                last_error = "сервис не вернул результат за отведённое время"
                continue
            return result

        return CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text=last_error)

    async def _poll(self, client: httpx.AsyncClient, token: str, inn: str) -> CheckResult | None:

        delay = 0.0
        elapsed = 0.0
        while elapsed < self._timeout:
            if delay:
                await asyncio.sleep(delay)
                elapsed += delay
            delay = min(delay + 1.0, 10.0) if delay else 1.0

            response = await client.get(f"/search-result/{token}", params={"r": int(time.time() * 1000)})
            response.raise_for_status()
            payload = response.json()
            rows: list[dict[str, Any]] = payload.get("rows") or []
            if not rows:
                continue  # ещё не готово — та же механика, что и у fns_inn

            row = rows[0]
            total = int(row.get("tot") or 0)
            is_ip = total > 0
            status = CheckStatus.OK if is_ip else CheckStatus.NO_DATA
            return CheckResult(
                status=status, response_raw=payload,
                parsed={"inn": inn, "is_individual_entrepreneur": is_ip},
            )
        return None
