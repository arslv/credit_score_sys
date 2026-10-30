"""Перечень террористов и экстремистов — с открытой страницы Росфинмониторинга
(по решению владельца: без личного кабинета, ежедневно).

Страница (`rosfinmon_page_url`) — HTML-список организаций и физических лиц.
Берём только физлиц: у записи физлица есть дата рождения «ДД.ММ.ГГГГ г.р.»,
у организации её нет — по этому признаку и отличаем, независимо от раздела
страницы. Прежние фамилии/имена в скобках — отдельными строками перечня с
той же датой рождения: человек мог подать заявку под любым из них.

Запрос честный: свой User-Agent, без подделки браузера и обхода защиты
(как и у Федресурса — не обходим). Если сайт ответил отказом (403 из-за
иностранного IP или защиты от ботов) — сбой загрузки, таблица перечня не
трогается, сбой виден в registry_sync_log и /health.

Сертификат сайта выпущен российским УЦ Минцифры — проверяем по нему
(`rosfinmon_ca_bundle_path`), а не отключаем проверку.

Защита от пустой страницы: если физлиц меньше `rosfinmon_page_min_persons`,
это не перечень (заглушка, страница ошибки, сменилась вёрстка) — сбой, а не
замена всей таблицы на пару строк."""

import csv
import io
import re
import ssl
from datetime import datetime
from html.parser import HTMLParser

import certifi
import httpx

from app.registries.parsers import RegistryParseError

USER_AGENT = "DIALOG-Scoring registry sync (+daily list check)"

# «1. ФАМИЛИЯ ИМЯ ОТЧЕСТВО* (ПРЕЖНЯЯ ФАМИЛИЯ ИМЯ ОТЧЕСТВО), 01.02.1990 г.р. , место рождения;»
_PERSON_RE = re.compile(
    r"^\s*(?:\d+\s*[.)]\s*)?(?P<names>(?:[^,(]|\([^)]*\))+?)\s*,\s*(?P<dob>\d{2}\.\d{2}\.\d{4})\s*г\.?\s*р\.?",
    re.IGNORECASE,
)
_ALIASES_RE = re.compile(r"\(([^)]*)\)")


class _TextBlocks(HTMLParser):
    """Текст каждого элемента списка (li) или абзаца/строки (p, div, br) —
    одна запись перечня на блок."""

    _BLOCK_TAGS = {"li", "p", "div", "tr", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._current: list[str] = []

    def _flush(self) -> None:
        text = " ".join("".join(self._current).split())
        if text:
            self.blocks.append(text)
        self._current = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag) -> None:
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data) -> None:
        self._current.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _clean_name(raw: str) -> str:
    # «*» у фамилии — пометка на сайте, не часть имени.
    return " ".join(raw.replace("*", " ").replace(";", " ").split()).upper()


def parse_fedsfm_persons(content: bytes, *, min_persons: int) -> list[dict[str, str]]:
    """Физлица со страницы перечня: [{full_name, birth_date (ISO)}], с
    прежними именами отдельными строками, без повторов."""
    html = content.decode("utf-8", errors="replace")
    parser = _TextBlocks()
    parser.feed(html)
    parser.close()

    seen: set[tuple[str, str]] = set()
    persons: list[dict[str, str]] = []
    person_count = 0
    for block in parser.blocks:
        match = _PERSON_RE.match(block)
        if not match:
            continue
        birth_date = datetime.strptime(match.group("dob"), "%d.%m.%Y").date().isoformat()
        names_part = match.group("names")
        aliases = [a for group in _ALIASES_RE.findall(names_part) for a in re.split(r"[;,]", group)]
        main = _ALIASES_RE.sub(" ", names_part)
        person_count += 1
        for name in [main, *aliases]:
            full_name = _clean_name(name)
            # Одно слово — не ФИО (обрывок вёрстки), сверять не по чему.
            if len(full_name.split()) < 2 or (full_name, birth_date) in seen:
                continue
            seen.add((full_name, birth_date))
            persons.append({"full_name": full_name, "birth_date": birth_date})

    if person_count < min_persons:
        raise RegistryParseError(
            f"на странице перечня найдено физлиц: {person_count}, ожидалось не меньше {min_persons} — "
            "похоже, это не перечень (ошибка сайта или сменилась вёрстка); таблица не заменена"
        )
    return persons


def persons_to_sanctions_csv(persons: list[dict[str, str]]) -> bytes:
    """В наш формат реестра (app/registries/parsers.py::parse_sanctions_csv),
    перечень Т/Э — list_type=te."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["list_type", "full_name", "birth_date"], lineterminator="\n")
    writer.writeheader()
    for p in persons:
        writer.writerow({"list_type": "te", "full_name": p["full_name"], "birth_date": p["birth_date"]})
    return out.getvalue().encode("utf-8")


def _ssl_context(ca_bundle_path: str) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    if ca_bundle_path:
        context.load_verify_locations(cafile=ca_bundle_path)
    return context


class FedsfmPageDownloader:
    """Скачивает страницу перечня и отдаёт её уже в нашем CSV — дальше общий
    путь синхронизации (parse_sanctions_csv → атомарная замена таблицы)."""

    def __init__(self, url: str, *, ca_bundle_path: str, min_persons: int, timeout: float = 60.0) -> None:
        self._url = url
        self._ca_bundle_path = ca_bundle_path
        self._min_persons = min_persons
        self._timeout = timeout

    async def fetch_persons(self) -> list[dict[str, str]]:
        if not self._url:
            raise ValueError("адрес страницы перечня (ROSFINMON_PAGE_URL) не задан")
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            verify=_ssl_context(self._ca_bundle_path),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self._url)
            if response.status_code != 200:
                raise RuntimeError(
                    f"сайт Росфинмониторинга ответил {response.status_code} — перечень не обновлён "
                    "(403 обычно значит запрос не с российского IP или отказ защиты сайта)"
                )
        return parse_fedsfm_persons(response.content, min_persons=self._min_persons)

    async def download(self, registry: str) -> bytes:
        if registry != "rosfinmon_sanctions":
            raise ValueError(f"страница Росфинмониторинга — только для перечня, не для {registry!r}")
        return persons_to_sanctions_csv(await self.fetch_persons())


class RoutingDownloader:
    """Свой источник для отдельных реестров, остальные — общим загрузчиком."""

    def __init__(self, routes: dict[str, object], default: object) -> None:
        self._routes = routes
        self._default = default

    async def download(self, registry: str) -> bytes:
        return await self._routes.get(registry, self._default).download(registry)  # type: ignore[attr-defined]
