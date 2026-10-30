"""Перечень Т/Э с открытой страницы Росфинмониторинга: разбор физлиц и
загрузка. Страница — синтетическая (вымышленные люди), в вёрстке как на
сайте: нумерованные записи, «*» у фамилии, прежние имена в скобках,
организации без даты рождения."""

import csv
import io

import httpx
import pytest
import respx
from sqlalchemy import select

from app.models import SanctionsList
from app.registries.fedsfm import (
    USER_AGENT,
    FedsfmPageDownloader,
    RoutingDownloader,
    parse_fedsfm_persons,
    persons_to_sanctions_csv,
)
from app.registries.jobs import sync_sanctions_list
from app.registries.parsers import RegistryParseError

URL = "https://lists.example.test/documents/terrorists-catalog-portal-act"

PAGE = """
<html><body>
<h3>Российские организации</h3>
<ol class="terrorist-list">
  <li>1. ВЫМЫШЛЕННАЯ ОРГАНИЗАЦИЯ "ТЕСТ";</li>
</ol>
<h3>Российские физические лица</h3>
<ol class="terrorist-list">
  <li>1. ТЕСТОВ ТЕСТ ТЕСТОВИЧ*, 01.02.1990 г.р. , Г. ВЫМЫШЛЕНСК;</li>
  <li>2. ПРИМЕРОВА ПРИМЕРА ПРИМЕРОВНА* (ОБРАЗЦОВА ПРИМЕРА ПРИМЕРОВНА), 03.04.1985 г.р. , С. ПРИМЕРНОЕ;</li>
  <li>3. ВЫМЫСЛОВ ВЫМЫСЕЛ (ВЫМЫСЛОВ ВЫМЫСЕЛ ВЫМЫСЛОВИЧ; ВЫДУМКИН ВЫМЫСЕЛ), 05.06.2000 г. р., Г. ТЕСТОВ;</li>
</ol>
<h3>Иностранные физические лица</h3>
<ol><li>1. FICTION JOHN, 07.08.1975 г.р. , NOWHERE;</li></ol>
</body></html>
""".encode("utf-8")


def test_parse_takes_only_persons_with_aliases():
    persons = parse_fedsfm_persons(PAGE, min_persons=1)
    pairs = {(p["full_name"], p["birth_date"]) for p in persons}

    assert pairs == {
        ("ТЕСТОВ ТЕСТ ТЕСТОВИЧ", "1990-02-01"),
        ("ПРИМЕРОВА ПРИМЕРА ПРИМЕРОВНА", "1985-04-03"),
        ("ОБРАЗЦОВА ПРИМЕРА ПРИМЕРОВНА", "1985-04-03"),
        ("ВЫМЫСЛОВ ВЫМЫСЕЛ", "2000-06-05"),
        ("ВЫМЫСЛОВ ВЫМЫСЕЛ ВЫМЫСЛОВИЧ", "2000-06-05"),
        ("ВЫДУМКИН ВЫМЫСЕЛ", "2000-06-05"),
        ("FICTION JOHN", "1975-08-07"),
    }
    # Организация (без даты рождения) не попала.
    assert not any("ОРГАНИЗАЦИЯ" in p["full_name"] for p in persons)


def test_parse_refuses_page_with_too_few_persons():
    # 4 физлица на странице, а ждём минимум 5 — не перечень, таблицу не трогаем.
    with pytest.raises(RegistryParseError, match="найдено физлиц: 4"):
        parse_fedsfm_persons(PAGE, min_persons=5)


def test_csv_is_our_registry_format():
    rows = list(csv.DictReader(io.StringIO(persons_to_sanctions_csv(parse_fedsfm_persons(PAGE, min_persons=1)).decode())))
    assert rows[0] == {"list_type": "te", "full_name": "ТЕСТОВ ТЕСТ ТЕСТОВИЧ", "birth_date": "1990-02-01"}


@respx.mock
async def test_downloader_sends_own_user_agent_and_returns_csv():
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=PAGE))
    downloader = FedsfmPageDownloader(URL, ca_bundle_path="", min_persons=1)

    content = await downloader.download("rosfinmon_sanctions")

    assert route.calls.last.request.headers["user-agent"] == USER_AGENT
    assert content.startswith(b"list_type,full_name,birth_date\n")


@respx.mock
async def test_downloader_fails_on_403_without_retrying_around_it():
    route = respx.get(URL).mock(return_value=httpx.Response(403, text="Forbidden"))
    downloader = FedsfmPageDownloader(URL, ca_bundle_path="", min_persons=1)

    with pytest.raises(RuntimeError, match="403"):
        await downloader.download("rosfinmon_sanctions")
    assert route.call_count == 1


async def test_downloader_is_only_for_sanctions_list():
    downloader = FedsfmPageDownloader(URL, ca_bundle_path="", min_persons=1)
    with pytest.raises(ValueError, match="mvd_invalid_passports"):
        await downloader.download("mvd_invalid_passports")


async def test_routing_downloader_uses_route_then_default():
    class Fixed:
        def __init__(self, body: bytes) -> None:
            self.body = body

        async def download(self, registry: str) -> bytes:
            return self.body

    router = RoutingDownloader({"rosfinmon_sanctions": Fixed(b"page")}, Fixed(b"file"))
    assert await router.download("rosfinmon_sanctions") == b"page"
    assert await router.download("mvd_invalid_passports") == b"file"


@respx.mock
async def test_sync_loads_parsed_page_into_sanctions_list(session_factory):
    respx.get(URL).mock(return_value=httpx.Response(200, content=PAGE))
    log = await sync_sanctions_list(session_factory, FedsfmPageDownloader(URL, ca_bundle_path="", min_persons=1))

    assert log.status == "ok"
    assert log.rows_loaded == 7
    async with session_factory() as session:
        entry = (
            await session.execute(select(SanctionsList).where(SanctionsList.full_name == "ОБРАЗЦОВА ПРИМЕРА ПРИМЕРОВНА"))
        ).scalar_one()
        assert entry.list_type == "te"
        assert str(entry.birth_date) == "1985-04-03"
