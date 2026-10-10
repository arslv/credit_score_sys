"""CSRF (T12, ваше уточнение): SameSite=Strict уже не даёт браузеру приложить
cookie к запросу, инициированному с чужого сайта, но это защита конкретно от
куки — не лишний слой не помешает. Все изменяющие запросы (не GET/HEAD/OPTIONS)
обязаны нести служебный заголовок: обычная HTML-форма его выставить не может,
а JS с чужого origin не может даже попытаться — наш CORS (app/main.py) не
разрешает этому запросу дойти с credentials с недоверенного origin.

Чистый ASGI-миддлварь, не BaseHTTPMiddleware: проверка — это только метод и один
заголовок, без надобности читать/буферизовать тело или оборачивать downstream-
вызов в отдельную задачу. BaseHTTPMiddleware сама заводит фоновую задачу на
запрос (слушает http.disconnect) — источник задач, переживающих ответ, что при
ASGITransport и pytest-asyncio (свой event loop на тест) ловится как
"Event loop is closed"/"attached to a different loop" в соседнем тесте."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class CsrfHeaderMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        # T18: вебхуки заказчика (.../api/v1/partner/*) — сервер-сервер по
        # статичному токену, без куки и без браузера; CSRF (защита конкретно
        # от куки чужого сайта) к ним не относится, а требовать от заказчика
        # наш внутренний служебный заголовок — лишнее условие в контракте.
        if scope["path"].startswith("/api/v1/partner/"):
            await self.app(scope, receive, send)
            return

        header_name = get_settings().csrf_header_name
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not headers.get(header_name.lower()):
            response = JSONResponse(
                status_code=403, content={"detail": f"missing required header {header_name}"}
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
