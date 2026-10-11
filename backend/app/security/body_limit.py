"""T21: лимит тела запроса в самом приложении — вторая линия после nginx
(client_max_body_size): в dev nginx нет, а api может оказаться доступен в
обход прокси. Понятный 413 с текстом, а не обрыв соединения.

Две проверки: Content-Length сразу (не читая тело), и подсчёт фактически
прочитанных байт — Content-Length может не прийти (chunked) или соврать."""

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings


def _too_large_detail(limit_mb: int) -> str:
    return (
        f"тело запроса больше допустимых {limit_mb} МБ — уменьшите фото "
        "(лимиты — docs/integration_contract.md)"
    )


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit_mb = get_settings().request_body_max_mb
        limit = limit_mb * 1024 * 1024
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length", b"").decode("latin-1")
        if declared.isdigit() and int(declared) > limit:
            response = JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, content={"detail": _too_large_detail(limit_mb)}
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # HTTPException FastAPI пробрасывает как есть (не превращает
                    # в 400 «ошибка разбора тела») -> JSON 413 тем же путём,
                    # что и любые другие ошибки API.
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=_too_large_detail(limit_mb))
            return message

        await self.app(scope, limited_receive, send)
