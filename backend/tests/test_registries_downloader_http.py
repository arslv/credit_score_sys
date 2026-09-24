from datetime import datetime, timezone

import pytest
import respx
from httpx import Response

from app.registries.downloader import HttpDownloader


@respx.mock
async def test_http_downloader_fetches_configured_url():
    route = respx.get("https://example.com/mvd/invalid_passports.csv").mock(
        return_value=Response(200, content=b"passport_series,passport_number\n0001,000001\n")
    )
    downloader = HttpDownloader({"mvd_invalid_passports": "https://example.com/mvd/invalid_passports.csv"})

    content = await downloader.download("mvd_invalid_passports")

    assert route.called
    assert content == b"passport_series,passport_number\n0001,000001\n"


@respx.mock
async def test_http_downloader_substitutes_date_placeholders():
    today = datetime.now(timezone.utc)
    expected_url = today.strftime("https://example.com/exports/%Y-%m-%d.csv")
    respx.get(expected_url).mock(return_value=Response(200, content=b"ok"))

    downloader = HttpDownloader({"rosfinmon_sanctions": "https://example.com/exports/%Y-%m-%d.csv"})
    content = await downloader.download("rosfinmon_sanctions")

    assert content == b"ok"


@respx.mock
async def test_http_downloader_raises_on_http_error():
    respx.get("https://example.com/broken.csv").mock(return_value=Response(403))
    downloader = HttpDownloader({"mvd_invalid_passports": "https://example.com/broken.csv"})

    with pytest.raises(Exception):
        await downloader.download("mvd_invalid_passports")


async def test_http_downloader_raises_clearly_on_unconfigured_registry():
    downloader = HttpDownloader({"mvd_invalid_passports": "https://example.com/mvd.csv"})
    with pytest.raises(ValueError, match="rosfinmon_sanctions"):
        await downloader.download("rosfinmon_sanctions")


async def test_http_downloader_raises_clearly_on_empty_url():
    downloader = HttpDownloader({"mvd_invalid_passports": ""})
    with pytest.raises(ValueError, match="не настроен"):
        await downloader.download("mvd_invalid_passports")
