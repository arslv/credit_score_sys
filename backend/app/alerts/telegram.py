"""T19: отправка оповещений в Telegram. Токен/chat_id — из env
(app/config.py), пустые выключают отправку. Никогда не бросает исключение
наружу (тот же принцип, что правило 4 CLAUDE.md для внешних источников) —
сбой самого Telegram не должен ронять задачу проверки здоровья
(app/tasks/alerts.py)."""

import httpx
import structlog

from app.config import Settings

logger = structlog.get_logger()


async def send_telegram_alert(settings: Settings, text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("telegram_alert_skipped_not_configured", text=text)
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url, json={"chat_id": settings.telegram_chat_id, "text": text},
            )
        # Правило 14 CLAUDE.md: ответ любого HTTP-вызова проверяется явно.
        if response.status_code != 200:
            logger.error(
                "telegram_alert_failed", status_code=response.status_code, body=response.text[:300],
            )
    except httpx.HTTPError as exc:
        logger.error("telegram_alert_failed", error=str(exc))
