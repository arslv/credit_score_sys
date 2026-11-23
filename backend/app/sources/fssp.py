"""ФССП, исполнительные производства (T09, переписано в T16 по итогам живой
разведки — капча-картинка на каждый поиск, решает только человек, ваше жёсткое
правило). Протокол полностью другой, чем в T09: официального API-ключа для
api-ip.fssp.gov.ru не существовало и само API закрыто с 2022 — вместо него
сайт is.fssp.gov.ru/ajax_search (до 10.2026 — is-go.fssp.gov.ru, теперь 502), два GET:

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
import struct
import uuid
import zlib
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from redis.asyncio import Redis

from app.sources.base import CheckResult, CheckStatus, Subject, missing_subject_field_error
from app.sources.throttle import throttle

_THROTTLE_KEY = "throttle:fssp"
_UA = "Mozilla/5.0 (compatible; DialogScoring/1.0)"
_SEARCH_URL = "https://is.fssp.gov.ru/ajax_search"

_JSONP_RE = re.compile(r"^\((.*)\);?\s*$", re.S)
# Картинка капчи: раньше PNG, с 10.2026 — JPEG.
_CAPTCHA_IMG_RE = re.compile(r"data:image/(?:png|jpe?g|gif);base64,([A-Za-z0-9+/=]+)")
# С 10.2026 капча привязана не к code_id в разметке, а к сессии сайта (кука
# connect.sid). Её значение и храним вместо code_id — с префиксом, чтобы
# отличать от старого протокола. Это техническая ссылка на диалог с сайтом
# (как и code_id), живёт минуты, персданных не содержит.
_SESSION_COOKIE = "connect.sid"
_SESSION_PREFIX = "sid:"
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


class FsspRateLimitedError(Exception):
    """Сайт ответил «попробуйте позже» — временная блокировка поиска
    (app/sources/fssp_cooldown.py). raw_response — ответ сайта (правило 3)."""

    def __init__(self, message: str, *, raw_response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


@dataclass(frozen=True)
class FsspSubmitOutcome:
    """kind: "ok" — код принят, check_result заполнен (в т.ч. "ничего не
    найдено" — это OK, не ошибка); "wrong_code" — код не принят (включая
    "лимит попыток превышен" — для вызывающего кода это то же самое: нужен
    новый code_id); "source_unavailable" — сайт/сеть подвели, check_result
    с SOURCE_UNAVAILABLE; "rate_limited" — сайт временно ограничил поиск
    («попробуйте позже»), check_result с SOURCE_UNAVAILABLE."""

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
    """Ответ сайта: раньше (is-go) — JSONP callback({...}), сейчас (is.fssp.gov.ru,
    с 10.2026) — обычный JSON {...}. Принимаем оба вида."""
    text = raw.strip()
    if text.startswith("{"):
        return json.loads(text)
    match = _JSONP_RE.match(text)
    if not match:
        raise ValueError("ни JSON, ни JSONP-обёртка callback(...)")
    return json.loads(match.group(1))


_RESULTS_TABLE_RE = re.compile(r'<table[^>]*class="[^"]*\blist\b[^"]*"[^>]*>(.*?)</table>', re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_AMOUNT_LINE_RE = re.compile(r"^(?P<subject>.+?):\s*(?P<amount>\d[\d\s]*(?:[.,]\d+)?)\s*руб", re.I)


def _cell_text(cell_html: str) -> str:
    """Текст ячейки: <br> — перенос строки, скрипты и теги долой."""
    text = re.sub(r"<script.*?</script>", "", cell_html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html_module.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _parse_results_table(table_html: str) -> list[dict[str, Any]]:
    """Живой формат is.fssp.gov.ru (сверено по первому реальному ответу с
    найденным производством, 09.11.2026): таблица class="list …", строка на
    производство, 8 колонок — должник; номер и дата ИП; реквизиты ИД; дата и
    причина окончания; оплата; предмет исполнения и сумма непогашенной
    задолженности; отдел; пристав. Предмет и сумма — строками «предмет: N
    руб.» (исполнительский сбор — отдельной строкой, отдельным производством).
    Оконченные производства (колонка 4 заполнена) — не долг, пропускаем."""
    productions: list[dict[str, Any]] = []
    for row in _ROW_RE.findall(table_html):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row)]
        if len(cells) < 6:
            continue  # заголовок (th) или чужая строка
        number = cells[1].splitlines()[0] if cells[1] else ""
        if cells[3]:
            continue  # окончено — дата и основание окончания
        lines = cells[5].splitlines()
        matched = [m for m in (_AMOUNT_LINE_RE.match(line) for line in lines) if m]
        if not matched:
            productions.append({"subject": cells[5].replace("\n", " "), "amount": "0", "raw": {"number": number}})
            continue
        for m in matched:
            productions.append({
                "subject": m.group("subject").strip(),
                "amount": re.sub(r"\s", "", m.group("amount")).replace(",", "."),
                "raw": {"number": number},
            })
    return productions


_IP_NUMBER_RE = re.compile(r"^(?P<number>.+?)\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})")


def parse_fssp_details(body_html: str) -> list[dict[str, Any]] | None:
    """Все производства из живого ответа (таблица is.fssp.gov.ru) — и
    действующие, и оконченные — с подробностями для карточки: номер и дата
    ИП, документ-основание, предмет и суммы, отдел; у оконченных — дата и
    основание окончания (статья 229-ФЗ, например «ст. 47 ч. 1 п. 1» —
    фактическое исполнение, то есть оплачено).

    Только для показа сотруднику: решение по заявке по-прежнему считается
    по действующим производствам (parse_fssp_result_html). Должника и
    пристава не возвращаем — должник и так известен, телефон пристава не
    нужен. None — таблицы нет (не найдено или старая разметка)."""
    table = _RESULTS_TABLE_RE.search(body_html)
    if table is None:
        return None
    details: list[dict[str, Any]] = []
    for row in _ROW_RE.findall(table.group(1)):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row)]
        if len(cells) < 6:
            continue
        head = cells[1].splitlines()[0] if cells[1] else ""
        number_match = _IP_NUMBER_RE.match(head)
        ended_lines = cells[3].splitlines()
        items = []
        for line in cells[5].splitlines():
            m = _AMOUNT_LINE_RE.match(line)
            if m:
                items.append({
                    "subject": m.group("subject").strip(),
                    "amount": re.sub(r"\s", "", m.group("amount")).replace(",", "."),
                })
        if not items and cells[5]:
            items.append({"subject": cells[5].replace("\n", " "), "amount": None})
        details.append({
            "number": number_match.group("number") if number_match else head,
            "date": number_match.group("date") if number_match else None,
            "document": " · ".join(cells[2].splitlines()) or None,
            "status": "ended" if cells[3] else "active",
            "ended_at": ended_lines[0] if ended_lines else None,
            "end_reason": " ".join(ended_lines[1:]) or None,
            "items": items,
            "department": cells[6].splitlines()[0] if len(cells) > 6 and cells[6] else None,
        })
    return details


def parse_fssp_result_html(body_html: str) -> list[dict[str, Any]] | None:
    """Разбирает HTML-фрагмент результата (внутри payload["data"]) в список
    открытых производств [{"subject": str, "amount": str}] — тот же вид, что
    потребляет app.engine.fssp.summarize_productions. None — структура не
    узнана (тогда error с сырым ответом); [] — таблица есть, но открытых
    производств нет (все окончены).

    Живой формат — _parse_results_table. Ниже — прежний разбор по
    синтетической фикстуре (оговорка T16 далее), оставлен на случай, если
    сайт вернёт ту разметку.

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
    table = _RESULTS_TABLE_RE.search(body_html)
    if table is not None:
        return _parse_results_table(table.group(1))

    # Не балансируем вложенные <div> регуляркой (ненадёжно) — вместо этого
    # идём по полям в порядке документа и группируем каждые predmet+summa в
    # одно производство (фиксированный порядок полей в своей же фикстуре).
    field_matches = re.findall(r'<div class="field" data-name="([^"]+)">([^<]*)</div>', body_html)
    if not field_matches:
        return None

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


def _is_blocked(body_html: str) -> bool:
    """Сайт временно не даёт искать (после неверных кодов / частых запросов):
    «Извините, что-то пошло не так. Попробуйте позже»."""
    lowered = body_html.lower()
    return "попробуйте позже" in lowered or "что-то пошло не так" in lowered


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


# --- Режим разработки (external_sources_mode = fake) ---------------------------

DEV_CAPTCHA_CODE = "000000"

# Цифра «0» 5×7 — другой в dev-капче не бывает.
_DEV_ZERO = (" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### ")


def _render_dev_captcha_png(code: str, scale: int = 4) -> bytes:
    """PNG (оттенки серого) с кодом крупными цифрами — без сторонних библиотек.
    Только для режима разработки: настоящую картинку отдаёт сайт ФССП."""
    glyph_w, glyph_h, gap, pad = 5 * scale, 7 * scale, 2 * scale, 3 * scale
    width = pad * 2 + len(code) * glyph_w + (len(code) - 1) * gap
    height = pad * 2 + glyph_h
    pixels = [[236] * width for _ in range(height)]
    for index, _digit in enumerate(code):
        left = pad + index * (glyph_w + gap)
        for row, line in enumerate(_DEV_ZERO):
            for col, cell in enumerate(line):
                if cell != "#":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        pixels[pad + row * scale + dy][left + col * scale + dx] = 40
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class DevFsspCaptchaSource:
    """ФССП в режиме разработки (external_sources_mode = fake): сайт не
    вызывается, капча — картинка с кодом 000000, этот код принимается, итог —
    «ничего не найдено». Без состояния: в отличие от FakeFsspCaptchaSource
    (тесты), источник создаётся заново на каждый запрос API, поэтому выданные
    code_id не запоминаются — узнаются по префиксу."""

    source_name = "fssp"

    async def search(self, subject: Subject, *, region_code: str) -> FsspCaptchaChallenge:
        missing = missing_subject_field_error(subject, FSSP_SEARCH_REQUIRED_FIELDS)
        if missing:
            raise FsspValidationError(missing)
        return FsspCaptchaChallenge(
            code_id=f"dev-{uuid.uuid4().hex}", captcha_png=_render_dev_captcha_png(DEV_CAPTCHA_CODE)
        )

    async def submit_code(
        self, *, code_id: str, code: str, subject: Subject, region_code: str,
    ) -> FsspSubmitOutcome:
        if not code_id.startswith("dev-") or code.strip() != DEV_CAPTCHA_CODE:
            return FsspSubmitOutcome(kind="wrong_code")
        return FsspSubmitOutcome(
            kind="ok",
            check_result=CheckResult(
                status=CheckStatus.OK, response_raw={"productions": [], "dev_mode": True},
                parsed={"productions": []},
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
                site_session = client.cookies.get(_SESSION_COOKIE)
        except Exception as exc:  # noqa: BLE001 — сеть/сайт не ответили вовсе, тела нет (ваше явное указание, п.5)
            raise FsspSearchError(f"сайт недоступен: {exc}") from exc

        body = html_module.unescape(payload.get("data", ""))
        if _is_blocked(body):
            raise FsspRateLimitedError("сайт ФССП временно ограничил поиск", raw_response=payload)
        code_id_match = _CODE_ID_RE.search(body)
        img_match = _CAPTCHA_IMG_RE.search(body)
        # Старый протокол — code_id в разметке; новый — сессия сайта в куке.
        code_id = code_id_match.group(1) if code_id_match else (
            f"{_SESSION_PREFIX}{site_session}" if site_session else None
        )
        if not code_id or not img_match:
            # Сайт РЕАЛЬНО ответил (payload получен), просто не капчей — это
            # сообщение показываем ТОЛЬКО здесь, не на сетевую ошибку выше
            # (п.5 ваших правок), и сохраняем сырой ответ (правило 3 CLAUDE.md).
            raise FsspSearchError(
                f"сайт ответил без капчи: {payload.get('err') or payload.get('e') or 'неизвестный формат ответа'}",
                raw_response=payload,
            )

        captcha_png = base64.b64decode(img_match.group(1) + "===")
        return FsspCaptchaChallenge(code_id=code_id, captcha_png=captcha_png)

    async def submit_code(
        self, *, code_id: str, code: str, subject: Subject, region_code: str,
    ) -> FsspSubmitOutcome:
        await throttle(self._redis, _THROTTLE_KEY, self._throttle_interval)
        params = {**self._base_params(subject, region_code), "code": code.strip()}
        cookies: dict[str, str] = {}
        if code_id.startswith(_SESSION_PREFIX):
            # Новый протокол: тот же запрос + код, в той же сессии сайта.
            cookies[_SESSION_COOKIE] = code_id[len(_SESSION_PREFIX):]
        else:
            params["code_id"] = code_id
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _UA}, cookies=cookies) as client:
                response = await client.get(_SEARCH_URL, params=params)
                response.raise_for_status()
                payload = _unwrap_jsonp(response.text)
        except Exception as exc:  # noqa: BLE001
            return FsspSubmitOutcome(
                kind="source_unavailable",
                check_result=CheckResult(status=CheckStatus.SOURCE_UNAVAILABLE, error_text=str(exc)),
            )

        body = html_module.unescape(payload.get("data", ""))

        # Неверный код — сайт сразу показывает новую капчу; «превышено…
        # попробуйте позже» и «что-то пошло не так» — уже блокировка: новую
        # капчу не запрашиваем, это её продлевает.
        if "captcha-popup" in body:
            return FsspSubmitOutcome(kind="wrong_code")
        if _is_blocked(body) or "превышен" in body.lower():
            return FsspSubmitOutcome(
                kind="rate_limited",
                check_result=CheckResult(
                    status=CheckStatus.SOURCE_UNAVAILABLE, response_raw={"html": body},
                    error_text="сайт ФССП временно ограничил поиск",
                ),
            )

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

        if productions is None:
            return FsspSubmitOutcome(
                kind="ok",
                check_result=CheckResult(
                    status=CheckStatus.ERROR, response_raw={"html": body},
                    error_text="непустой ответ, но не похож ни на «не найдено», ни на известную структуру таблицы",
                ),
            )
        if not productions:
            # Нашлись только оконченные производства — открытых долгов нет.
            return FsspSubmitOutcome(
                kind="ok",
                check_result=CheckResult(
                    status=CheckStatus.OK, response_raw={"html": body}, parsed={"productions": []},
                ),
            )

        return FsspSubmitOutcome(
            kind="ok",
            check_result=CheckResult(
                status=CheckStatus.HIT, response_raw={"html": body},
                parsed={"productions": productions},
            ),
        )
