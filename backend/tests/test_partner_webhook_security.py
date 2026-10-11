"""T21: безопасность интеграции с заказчиком — фото по ссылке (только
разрешённые хосты, только https, таймаут, лимит размера, только JPEG/PNG),
лимит тела запроса с понятным 413, токен через hmac.compare_digest.
Внешние HTTP — через respx (правило 10 CLAUDE.md)."""

import base64

import httpx
import pytest
import respx
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Response

from app.api import partner_webhooks
from app.api.partner_webhooks import PhotoRefIn, _fetch_photo
from app.config import get_settings

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
_JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
_HOST = "photos.partner.example"


@pytest.fixture
def photo_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "partner_photo_hosts", f"{_HOST}, cdn.partner.example")
    monkeypatch.setattr(settings, "partner_photo_allow_http", False)
    monkeypatch.setattr(settings, "partner_photo_max_bytes", 1000)
    monkeypatch.setattr(settings, "partner_photo_timeout_seconds", 2.0)
    return settings


async def _status_of(ref: PhotoRefIn) -> int:
    with pytest.raises(HTTPException) as exc_info:
        await _fetch_photo(ref)
    return exc_info.value.status_code


# --- фото по ссылке -------------------------------------------------------------


async def test_photo_url_from_allowed_https_host_is_downloaded(photo_settings):
    with respx.mock:
        respx.get(f"https://{_HOST}/p/1.png").mock(
            return_value=Response(200, content=_PNG, headers={"content-type": "image/png"})
        )
        assert await _fetch_photo(PhotoRefIn(url=f"https://{_HOST}/p/1.png")) == _PNG
        respx.get(f"https://{_HOST}/p/2.jpg").mock(
            return_value=Response(200, content=_JPEG, headers={"content-type": "image/jpeg; charset=binary"})
        )
        assert await _fetch_photo(PhotoRefIn(url=f"https://{_HOST}/p/2.jpg")) == _JPEG


async def test_photo_url_host_not_in_allowlist_rejected_without_request(photo_settings):
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(return_value=Response(200, content=_PNG))
        for url in (
            "https://evil.example/p.png",
            f"https://{_HOST}.evil.example/p.png",  # не поддомен-обход
            f"https://evil.{_HOST}/p.png",
            "https://169.254.169.254/latest/meta-data",
        ):
            assert await _status_of(PhotoRefIn(url=url)) == 422
        assert not route.called  # даже не ходили


async def test_photo_url_requires_https(photo_settings):
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(return_value=Response(200, content=_PNG))
        assert await _status_of(PhotoRefIn(url=f"http://{_HOST}/p.png")) == 422
        assert await _status_of(PhotoRefIn(url=f"ftp://{_HOST}/p.png")) == 422
        assert not route.called


async def test_photo_url_rejected_when_no_hosts_configured(photo_settings, monkeypatch):
    monkeypatch.setattr(photo_settings, "partner_photo_hosts", "")
    assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/p.png")) == 422


async def test_photo_url_wrong_content_type_rejected(photo_settings):
    with respx.mock:
        respx.get(f"https://{_HOST}/p.html").mock(
            return_value=Response(200, content=_PNG, headers={"content-type": "text/html"})
        )
        assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/p.html")) == 415


async def test_photo_url_declared_image_but_bytes_are_not_image(photo_settings):
    with respx.mock:
        respx.get(f"https://{_HOST}/p.png").mock(
            return_value=Response(200, content=b"<html>not an image</html>", headers={"content-type": "image/png"})
        )
        assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/p.png")) == 415


async def test_photo_url_too_large_by_content_length(photo_settings):
    with respx.mock:
        respx.get(f"https://{_HOST}/big.png").mock(
            return_value=Response(200, content=_PNG + b"x" * 2000, headers={"content-type": "image/png"})
        )
        assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/big.png")) == 413


async def test_photo_url_too_large_without_content_length(photo_settings):
    async def body():
        yield _PNG
        for _ in range(50):
            yield b"x" * 100

    with respx.mock:
        respx.get(f"https://{_HOST}/stream.png").mock(
            return_value=Response(200, content=body(), headers={"content-type": "image/png"})
        )
        assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/stream.png")) == 413


async def test_photo_url_timeout_is_a_clear_error(photo_settings):
    with respx.mock:
        respx.get(f"https://{_HOST}/slow.png").mock(side_effect=httpx.ReadTimeout("timeout"))
        assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/slow.png")) == 422


async def test_photo_url_redirect_is_not_followed(photo_settings):
    with respx.mock:
        respx.get(f"https://{_HOST}/r.png").mock(
            return_value=Response(302, headers={"location": "https://evil.example/p.png"})
        )
        evil = respx.get("https://evil.example/p.png").mock(return_value=Response(200, content=_PNG))
        assert await _status_of(PhotoRefIn(url=f"https://{_HOST}/r.png")) == 422
        assert not evil.called


async def test_photo_url_uses_configured_timeout(photo_settings, monkeypatch):
    seen = {}
    original = httpx.AsyncClient.__init__

    def spy(self, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy)
    with respx.mock:
        respx.get(f"https://{_HOST}/p.png").mock(return_value=Response(200, content=_PNG, headers={"content-type": "image/png"}))
        await _fetch_photo(PhotoRefIn(url=f"https://{_HOST}/p.png"))
    assert seen["timeout"] == 2.0


# --- фото в base64 ----------------------------------------------------------------


async def test_base64_photo_must_be_jpeg_or_png(photo_settings):
    assert await _fetch_photo(PhotoRefIn(base64=base64.b64encode(_PNG).decode())) == _PNG
    assert await _status_of(PhotoRefIn(base64=base64.b64encode(b"GIF89a" + b"x" * 10).decode())) == 415


async def test_base64_photo_too_large(photo_settings):
    assert await _status_of(PhotoRefIn(base64=base64.b64encode(_PNG + b"x" * 2000).decode())) == 413


# --- лимит тела запроса и токен ----------------------------------------------------


async def _post_applications(content: bytes, headers: dict) -> Response:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        return await client.post("/api/v1/partner/applications", content=content, headers=headers)


_MB = 1024 * 1024


async def test_request_body_over_limit_gets_clear_413(monkeypatch):
    monkeypatch.setattr(get_settings(), "request_body_max_mb", 1)
    headers = {
        "Authorization": f"Bearer {get_settings().partner_inbound_token}", "Content-Type": "application/json",
    }
    response = await _post_applications(b'{"x": "' + b"a" * (2 * _MB) + b'"}', headers)
    assert response.status_code == 413
    assert response.json()["detail"].startswith("тело запроса больше допустимых 1 МБ")


async def test_request_body_over_limit_without_content_length_gets_413(monkeypatch):
    """Content-Length может не прийти (chunked) — считаем фактически
    прочитанные байты."""
    monkeypatch.setattr(get_settings(), "request_body_max_mb", 1)

    async def chunks():
        yield b'{"external_id": "'
        for _ in range(20):
            yield b"a" * (_MB // 10)
        yield b'"}'

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        response = await client.post(
            "/api/v1/partner/applications", content=chunks(),
            headers={"Authorization": f"Bearer {get_settings().partner_inbound_token}", "Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert "больше допустимых" in response.json()["detail"]


async def test_inbound_token_compared_with_hmac_compare_digest(monkeypatch):
    calls = []
    original = partner_webhooks.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(partner_webhooks.hmac, "compare_digest", spy)
    response = await _post_applications(
        b"{}", {"Authorization": "Bearer wrong-token", "Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert calls and calls[0][0] == b"wrong-token"

    # Пустой/отсутствующий токен — 401 тоже.
    assert (await _post_applications(b"{}", {"Content-Type": "application/json"})).status_code == 401
