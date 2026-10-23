"""Правило 7 CLAUDE.md: httpx не пишет в лог адреса запросов — в них ФИО и
дата рождения клиента (запросы к ФССП/ФНС)."""

import logging

from app.logging import configure_logging


def test_httpx_request_urls_are_not_logged_at_info():
    configure_logging()
    for name in ("httpx", "httpcore"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
