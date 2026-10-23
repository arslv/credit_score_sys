import logging
import sys

import structlog

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = logging.getLevelName(settings.log_level.upper())

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    # httpx/httpcore на INFO пишут полный URL запроса, а в запросах к внешним
    # сайтам (ФССП, ФНС) в адресе — ФИО и дата рождения клиента. Персданные в
    # логах запрещены (CLAUDE.md, правило 7) — только предупреждения и ошибки.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
