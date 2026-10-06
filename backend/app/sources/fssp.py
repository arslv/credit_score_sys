"""ФССП, исполнительные производства (T09, переписано в T16 по итогам живой
разведки — капча-картинка на каждый поиск, решает только человек, ваше жёсткое
правило). Протокол полностью другой, чем в T09: официального API-ключа для
api-ip.fssp.gov.ru не существовало и само API закрыто с 2022 — вместо него
сайт is-go.fssp.gov.ru/ajax_search, два GET:

1. search() — параметры ФИО/дата рождения/регион (is[region_id][0] — код из
   config/fssp_regions.yaml, НЕ префикс серии паспорта, ваше явное указание) ->
   JSONP {"data": "<html с картинкой капчи и code_id>", ...}.
2. submit_code() — те же параметры + code_id + введённый человеком код ->
   JSONP {"data": "<html с результатом или просьбой о новом коде>", ...}.

Кук не требуется (подтверждено дважды живым тестом на разведке). Неверный код
даёт НЕ мягкое "попробуйте снова", а "Количество неверных попыток ввода кода
превышено" сразу — но это ограничение по конкретной попытке/code_id, не по
IP: свежий code_id (новый search()) тут же принимает верный код (подтверждено
живым тестом). Поэтому submit_code() при неверном коде не пытается
"повторить" сам — возвращает wrong_code, автоматический новый search() делает
вызывающий код (app/api/fssp.py), как вы и решили.

Классификация производств (sources.fssp.classification, app.engine.fssp.
summarize_productions) — НЕ здесь: этот модуль возвращает только сырой список
productions, классификацию считает вызывающий код (app/api/fssp.py). Иначе
app.sources, низкоуровневый слой, зависел бы от app.engine, а app.engine —
от app.sources (через factory) — циклический импорт."""

import base64
import html as html_module
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from redis.asyncio import Redis

from app.sources.base import CheckResult, CheckStatus, Subject, missing_subject_field_error
from app.sources.throttle import throttle

_THROTTLE_KEY = "throttle:fssp"
_UA = "Mozilla/5.0 (compatible; DialogScoring/1.0)"
_SEARCH_URL = "https://is-go.fssp.gov.ru/ajax_search"

_JSONP_RE = re.compile(r"^\((.*)\);?\s*$", re.S)
_CAPTCHA_IMG_RE = re.compile(r"data:image/png;base64,([A-Za-z0-9+/=]+)")
_CODE_ID_RE = re.compile(r"code_id=([A-Za-z0-9_\-]+)")


class FsspSearchError(Exception):
    """search() (запрос 1) не удался — сеть/сайт недоступен ИЛИ сайт ответил,
    но без ожидаемой капчи (изменился формат страницы и т.п.) — вызывающий
    код решает сам: SOURCE_UNAVAILABLE check, без картинки, см. app/api/fssp.py.
    raw_response — то, что успели получить от сайта (если успели — правило 3
    CLAUDE.md, сырой ответ сохраняется даже при ошибке разбора); для сетевой
    ошибки/таймаута raw_response=None, ответа не было вовсе (ваше явное
    указание, п.5 правок: разные причины — разные сообщения, не одно общее
    "не нашли капчу" на любой сбой)."""

    def __init__(self, message: str, *, raw_response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


class FsspValidationError(Exception):
    """Субъект неполный (пустое ФИО/дата рождения) — сайт вообще не
    запрашивался (ваше явное указание, п.2 правок: это не 'сайт недоступен',
    отдельная, понятная причина, видимая оператору без похода на внешний сайт)."""


@dataclass(frozen=True)
class FsspCaptchaChallenge:
    code_id: str
    captcha_png: bytes


@dataclass(frozen=True)
class FsspSubmitOutcome:
    """kind: "ok" — код принят, check_result заполнен (в т.ч. "ничего не
    найдено" — это OK, не ошибка); "wrong_code" — код не принят (включая
    "лимит попыток превышен" — для вызывающего кода это то же самое: нужен
    новый code_id); "source_unavailable" — сайт/сеть подвели, check_result
    с SOURCE_UNAVAILABLE."""

    kind: str
    check_result: CheckResult | None = None


class FsspCaptchaSource(Protocol):
    source_name: str

    async def search(self, subject: Subject, *, region_code: str) -> FsspCaptchaChallenge:
        ...

    async def submit_code(
        self, *, code_id: str, code: str, subject: Subject, region_code: str,
    ) -> FsspSubmitOutcome:
        ...


def _unwrap_jsonp(raw: str) -> dict[str, Any]:
    match = _JSONP_RE.match(raw.strip())
    if not match:
        raise ValueError("не JSONP-обёртка callback(...)")
    return json.loads(match.group(1))


def parse_fssp_result_html(body_html: str) -> list[dict[str, Any]]:
    """Разбирает HTML-фрагмент результата (внутри payload["data"]) в список
    производств [{"subject": str, "amount": str}] — тот же вид, что уже
    потребляет app.engine.fssp.summarize_productions.

    ВАЖНАЯ ОГОВОРКА (T16): оба живых теста на разведке дали «ничего не
    найдено» — реального образца HTML с НАЙДЕННЫМ производством нет. Парсер
    ниже рассчитан на структуру, которую вы описали (должник, номер ИП,
    реквизиты ИД, дата и основание окончания, предмет исполнения и сумма,
    отдел, пристав) в виде повторяющихся блоков `<div class="production">`
    с полями `<div class="field" data-name="...">значение</div>` внутри —
    то есть на СОБСТВЕННУЮ синтетическую фикстуру (tests/fixtures/fssp_*.html),
    не на проверенный вживую формат. Первое реальное совпадение в проде нужно
    сверить и поправить парсер, если структура сайта отличается — до этого
    момента "не разобрали" уходит в error с сырым HTML, оператор видит его
    сам (ваше явное указание), автоотказа отсюда не бывает ни при каком
    исходе разбора."""
    # Не балансируем вложенные <div> регуляркой (ненадёжно) — вместо этого
    # идём по полям в порядке документа и группируем каждые predmet+summa в
    # одно производство (фиксированный порядок полей в своей же фикстуре).
    field_matches = re.findall(r'<div class="field" data-name="([^"]+)">([^<]*)</div>', body_html)

    productions: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for name, value in field_matches:
        if name == "predmet" and "predmet" in current:
            # Новое производство началось раньше, чем закрылось предыдущее
            # (не должно случаться при валидной фикстуре — защищаемся).
            productions.append(_finish_production(current))
            current = {}
        current[name] = value
        if name == "summa":
            productions.append(_finish_production(current))
            current = {}
    if current:
        productions.append(_finish_production(current))
    return productions


def _finish_production(fields: dict[str, str]) -> dict[str, Any]:
    subject_text = fields.get("predmet", "")
    amount = fields.get("summa", "0").replace(" ", "").replace(",", ".")
    return {"subject": subject_text, "amount": amount, "raw": dict(fields)}


def _is_not_found(body_html: str) -> bool:
    return "ничего не найдено" in body_html.lower()


def _is_wrong_code(body_html: str) -> bool:
    lowered = body_html.lower()
    return "captcha-popup" in body_html or "превышен" in lowered or "неверн" in lowered


FSSP_SEARCH_REQUIRED_FIELDS = ("last_name", "first_name", "birth_date")


class FakeFsspCaptchaSource:
    """Для тестов: captcha_by_code_id — какой код считается верным для
    каждого заранее заданного code_id; productions_by_code_id — что вернуть
    при верном коде (пусто -> "ничего не найдено")."""

    source_name = "fssp"

    def __init__(
        self, *,
        valid_code: str = "000000",
        productions: list[dict[str, Any]] | None = None,
        raise_on_search: bool = False,
        wrong_code_once: bool = False,
    ) -> None:
        self._valid_code = valid_code
        self._productions = productions or []
        self._raise_on_search = raise_on_search
        self._wrong_code_once = wrong_code_once
        self._issued_code_ids: set[str] = set()
        self._counter = 0
        self.search_calls: list[Subject] = []
        self.submit_calls: list[tuple[str, str]] = []

    async def search(self, subject: Subject, *, region_code: str) -> FsspCaptchaChallenge:
        self.search_calls.append(subject)
        missing = missing_subject_field_error(subject, FSSP_SEARCH_REQUIRED_FIELDS)
        if missing:
            raise FsspValidationError(missing)
        if self._raise_on_search:
            raise FsspSearchError("fake fssp search failed")
        self._counter += 1
        code_id = f"fake-code-id-{self._counter}"
        self._issued_code_ids.add(code_id)
        # 1x1 белый PNG — валидный файл, не настоящая капча (для тестов неважно).
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        return FsspCaptchaChallenge(code_id=code_id, captcha_png=tiny_png)

    async def submit_code(
        self, *, code_id: str, code: str, subject: Subject, region_code: str,
    ) -> FsspSubmitOutcome:
        self.submit_calls.append((code_id, code))
        if code_id not in self._issued_code_ids:
            return FsspSubmitOutcome(kind="wrong_code")
        if code != self._valid_code or self._wrong_code_once:
            self._issued_code_ids.discard(code_id)  # старый code_id больше не принимаем (T16, ваше указание)
            return FsspSubmitOutcome(kind="wrong_code")

        self._issued_code_ids.discard(code_id)
        status = CheckStatus.HIT if self._productions else CheckStatus.OK
        return FsspSubmitOutcome(
            kind="ok",
            check_result=CheckResult(
                status=status, response_raw={"productions": self._productions},
                parsed={"productions": self._productions},
            ),
        )


class FsspCaptchaHttpSource:
    source_name = "fssp"

    def __init__(self, *, redis: Redis, request_timeout_seconds: float, throttle_interval_seconds: float) -> None:
        self._redis = redis
        self._timeout = request_timeout_seconds
        self._throttle_interval = throttle_interval_seconds

    def _base_params(self, subject: Subject, region_code: str) -> dict[str, str]:
        return {
            "system": "ip",
            "is[extended]": "1",
            "is[variant]": "1",
            "is[region_id][0]": region_code,
            "is[last_name]": subject.last_name or "",
            "is[first_name]": subject.first_name or "",
            "is[patronymic]": subject.middle_name or "",
            "is[date]": subject.birth_date.strftime("%d.%m.%Y") if subject.birth_date else "",
        }

    async def search(self, subject: Subject, *, region_code: str) -> FsspCaptchaChallenge:
        missing = missing_subject_field_error(subject, FSSP_SEARCH_REQUIRED_FIELDS)
        if missing:
            raise FsspValidationError(missing)

        await throttle(self._redis, _THROTTLE_KEY, self._throttle_interval)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _UA}) as client:
                response = await client.get(_SEARCH_URL, params=self._base_params(subject, region_code))
                response.raise_for_status()
                payload = _unwrap_jsonp(response.text)
        except Exception as exc:  # noqa: BLE001 — сеть/сайт не ответили вовсе, тела нет (ваше явное указание, п.5)
            raise FsspSearchError(f"сайт недоступен: {exc}") from exc

        body = html_module.unescape(payload.get("data", ""))
        code_id_match = _CODE_ID_RE.search(body)
        img_match = _CAPTCHA_IMG_RE.search(body)
        if not code_id_match or not img_match:
            # Сайт РЕАЛЬНО ответил (payload получен), просто не капчей — это
            # сообщение показываем ТОЛЬКО здесь, не на сетевую ошибку выше
            # (п.5 ваших правок), и сохраняем сырой ответ (правило 3 CLAUDE.md).
            raise FsspSearchError(
                f"сайт ответил без капчи: {payload.get('err') or payload.get('e') or 'неизвестный формат ответа'}",
                raw_response=payload,
            )

        captcha_png = base64.b64decode(img_match.group(1) + "===")
        return FsspCaptchaChallenge(code_id=code_id_match.group(1), captcha_png=captcha_png)

    async def submit_code(
        self, *, code_id: str, code: str, subject: Subject, region_code: str,
    ) -> FsspSubmitOutcome:
        await throttle(self._redis, _THROTTLE_KEY, self._throttle_interval)
        params = {**self._base_params(subject, region_code), "code_id": code_id, "code": code.strip()}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _UA}) as client:
                response = await client.get(_SEARCH_URL, params=params)
                response.raise_for_status()
                payload = _unwrap_jsonp(response.text)
        except Exception as exc:  # noqa: BLE001
            return FsspSubmitOutcome(
                kind="source_unavailable",
                check_result=CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text=str(exc)),
            )

        body = html_module.unescape(payload.get("data", ""))

        if _is_wrong_code(body):
            return FsspSubmitOutcome(kind="wrong_code")

        if _is_not_found(body):
            return FsspSubmitOutcome(
                kind="ok",
                check_result=CheckResult(
                    status=CheckStatus.OK, response_raw={"html": body}, parsed={"productions": []},
                ),
            )

        try:
            productions = parse_fssp_result_html(body)
        except Exception as exc:  # noqa: BLE001 — не разобрали -> error с сырым ответом, не падаем (ваше явное указание)
            return FsspSubmitOutcome(
                kind="ok",
                check_result=CheckResult(
                    status=CheckStatus.ERROR, response_raw={"html": body}, error_text=f"не удалось разобрать: {exc}",
                ),
            )

        if not productions:
            return FsspSubmitOutcome(
                kind="ok",
                check_result=CheckResult(
                    status=CheckStatus.ERROR, response_raw={"html": body},
                    error_text="непустой ответ, но не похож ни на «не найдено», ни на известную структуру таблицы",
                ),
            )

        return FsspSubmitOutcome(
            kind="ok",
            check_result=CheckResult(
                status=CheckStatus.HIT, response_raw={"html": body},
                parsed={"productions": productions},
            ),
        )
