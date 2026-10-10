"""T18: интеграция по новой схеме (push). PartnerClient был протоколом опроса
их API (list/get/download по расписанию) — его больше нет, заявки и анкеты
к нам приходят вебхуками (app/api/partner_webhooks.py). Что осталось здесь —
исключения исходящей доставки (app/integrations/partner_notifier.py)."""


class PartnerUnavailableError(Exception):
    """Сетевая ошибка, таймаут или 5xx — временная проблема, стоит повторить попытку."""


class PartnerConflictError(Exception):
    """Их эндпоинт ответил 409 — статус там уже не совпадает с prev_status,
    который мы отправили (оператор изменил его вручную, правило 12 CLAUDE.md).
    current_status/reason — из ТЕЛА их 409-ответа (контракт, который мы же им
    и задаём, см. docs/integration_contract.md) — отдельного запроса "а какой
    у вас сейчас статус" больше не нужно, раньше для этого был get_application."""

    def __init__(self, current_status: str | None, reason: str | None = None) -> None:
        self.current_status = current_status
        self.reason = reason
        super().__init__(f"status conflict: current_status={current_status!r}")


class PartnerBadRequestError(Exception):
    """Их эндпоинт отклонил запрос как некорректный (4xx, кроме конфликта статуса)."""
