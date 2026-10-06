from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dialog:dialog@db:5432/dialog"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"

    environment: str = "development"

    # T11: защита от случайных фейковых провайдеров в бою (app/startup_guard.py).
    # По умолчанию — production, самый строгий режим: забытая переменная не
    # должна тихо означать самый мягкий. dev/test — permissive.
    app_env: Literal["dev", "test", "production"] = "production"

    # Интеграция с системой заказчика (см. fake_partner на этапе, пока их API неизвестен)
    partner_base_url: str = "http://fake_partner:8000"
    partner_token: str = "dev-token"
    partner_poll_interval_seconds: int = 30

    # Повторы обращений к их API при недоступности
    integration_retry_max_attempts: int = 5
    integration_retry_base_delay_seconds: float = 1.0
    integration_retry_max_delay_seconds: float = 30.0

    # Файловое хранилище фото (локальный каталог, за интерфейсом Storage)
    storage_dir: str = "/data/storage"

    # Распознавание фото паспорта (T05)
    anthropic_api_key: str = ""
    ocr_model: str = "claude-sonnet-5"
    # anthropic (по умолчанию, реальный ключ) | fixture — для scripts/e2e_check.py:
    # прогон через настоящие Celery-задачи без реального ключа Anthropic,
    # см. app/ocr/factory.py.
    ocr_provider_mode: str = "anthropic"
    ocr_fixture_path: str = ""
    # Технический порог нечёткого сравнения ФИО (допуск на опечатки) — не бизнес-правило
    # вида "возраст/сумма/срок" из правила 2 CLAUDE.md, поэтому не в YAML-конфиге.
    ocr_name_match_min_ratio: float = 0.85
    # Провайдер распознавания может быть недоступен (сеть, не настроен ключ) —
    # сбой источника не должен останавливать заявку (тот же принцип, что
    # правило 4 CLAUDE.md для внешних источников, хотя OCR и не реализует
    # SourceBase буквально). Несколько попыток подряд, затем — оператору с
    # пометкой "паспорт не распознан", не бесконечное зависание в статусе ocr.
    ocr_max_attempts: int = 3
    ocr_retry_delay_seconds: float = 2.0
    # Защита на случай, если даже цикл повторов не отработал штатно (процесс
    # убит посреди вызова, сеть зависла без исключения и т.п.) — заявка,
    # дольше этого числа минут находящаяся в статусе "ocr", считается
    # зависшей и уводится к оператору тем же путём (app/tasks/stage_a.py),
    # а счётчик таких заявок виден на экране состояния системы (app/api/system.py).
    ocr_stuck_minutes: int = 30

    # Движок правил (T06) — активная версия выбирается по effective_from в ruleset_versions
    ruleset_path: str = "config/rulesets/dev.yaml"
    # T14: симуляция при редактировании правил — сколько последних заявок берём по
    # умолчанию (ТЗ, «Правила и пороги»: «последних 500 заявок») и на сколько
    # часов действительна одна симуляция для последующего сохранения (не бизнес-
    # порог из правила 2 CLAUDE.md — технический параметр гейта сохранения).
    ruleset_simulation_default_sample_size: int = 500
    ruleset_simulation_ttl_hours: int = 24

    # T15: импорт договоров/платежей/исторических заявок — формат выгрузки
    # неизвестен заранее, соответствие колонок только в конфиге.
    import_mapping_contracts_path: str = "config/import_mappings/contracts.yaml"
    import_mapping_payments_path: str = "config/import_mappings/payments.yaml"
    import_mapping_historical_applications_path: str = "config/import_mappings/historical_applications.yaml"
    import_mapping_payment_schedule_path: str = "config/import_mappings/payment_schedule.yaml"
    collections_config_path: str = "config/collections.yaml"
    # Ежедневная задача (ТЗ: «пересчитывается ежедневной задачей») — технический
    # интервал, не бизнес-порог; раз в сутки, как и реестры.
    delinquency_recompute_interval_seconds: int = 86400

    # Соответствие наших исходов статусам в системе заказчика — в конфиге, не в коде
    # (ТЗ, «Интеграция» -> «Соответствие статусов»)
    status_mapping_path: str = "config/status_mapping.yaml"

    # Оркестратор стадии А (T07)
    stage_a_poll_interval_seconds: int = 15

    # Синхронизатор реестров МВД/Росфинмониторинга (T08) — файлы кладутся сюда
    # вручную/полуавтоматически, см. app/registries/downloader.py
    registries_dir: str = "/data/registries"
    registries_sync_interval_seconds: int = 86400  # ежесуточно, как публикуют источники
    # local — читает файлы из registries_dir (по умолчанию, надёжный вариант);
    # http — качает по URL из *_registry_url. Рабочие URL не подтверждены (см.
    # app/registries/downloader.py) — просто так переключать в http нельзя.
    registries_downloader: str = "local"
    mvd_registry_url: str = ""
    rosfinmon_registry_url: str = ""

    # Справочник кодов подразделений (T08) — hflabs/fms-unit, статичный файл,
    # не синхронизируется периодически (см. docs/registries.md).
    department_codes_path: str = "config/reference/fms_unit.csv"

    # ФНС/ФССП (T16, заменяет старый блок T09 — Федресурс убран, ФССП больше
    # не опрашивается автоматически): таймауты/интервалы — технические
    # параметры, не бизнес-правило (правило 2 CLAUDE.md касается порогов вида
    # возраст/сумма/срок; ср. ocr_name_match_min_ratio выше). Классификация
    # типов производств ФССП, в отличие от этого, — business-правило и живёт
    # в YAML-конфиге (sources.fssp.classification).
    external_sources_mode: str = "fake"  # fake (по умолчанию) | http — см. app/sources/*.py

    # service.nalog.ru (fns_inn) и egrul.nalog.ru (fns_egrip) — одна платформа
    # ("uni", общий стек статики), обе формы умеют показать капчу при
    # превышении частоты запросов (разведка T16) — капчу решает только
    # человек (ваше жёсткое правило), поэтому мы её никогда не показываем и
    # не решаем: при captchaRequired=true просто откладываем попытку.
    # Официального числа для этого лимита нет (в отличие от statusnpd ниже) —
    # 20 с между запросами выбрано консервативно, не на основании измеренного
    # порога: единственный живой кейс, где я увидела капчу на разведке,
    # оказался статическим файлом-примером (/static/captcha-dialog.html), а
    # не подтверждённым откликом именно на мою частоту запросов — см.
    # docs/reports/T16.md, честно об этом сказано, порог не измерен.
    fns_shared_request_interval_seconds: float = 20.0
    fns_max_attempts: int = 5
    fns_retry_backoff_seconds: float = 20.0
    fns_request_timeout_seconds: float = 15.0
    # statusnpd.nalog.ru (fns_npd) — отдельный простой REST, без капчи на
    # разведке; задокументированный лимит ФНС — не больше 2 запросов в минуту
    # с одного IP (ваше сообщение с разведкой) — 30 с с запасом на джиттер.
    fns_npd_min_interval_seconds: float = 30.0
    fns_npd_request_timeout_seconds: float = 20.0

    # ФССП (is-go.fssp.gov.ru) — капчу вводит оператор (T16), без кук
    # (подтверждено дважды живым тестом). Интервал — консервативная защита от
    # собственного флуда с нашей стороны, не измеренный порог сайта.
    fssp_min_request_interval_seconds: float = 5.0
    fssp_request_timeout_seconds: float = 15.0
    fssp_regions_path: str = "config/fssp_regions.yaml"
    # Сессия "код показан, ждём ввода" считается истёкшей через столько минут
    # без ответа оператора — новый поиск можно начать, старая строка
    # fssp_captcha_sessions помечается expired.
    fssp_captcha_session_timeout_minutes: int = 15

    # БКИ (T11): договора с бюро нет и неизвестно, с каким бюро он будет —
    # отдельная настройка от external_sources_mode (у ФССП/ФНС хотя бы есть
    # гипотетический URL, у БКИ нет и этого). not_configured — единственный
    # безопасный дефолт; fake — для дев/тестов с синтетическими отчётами.
    bki_provider_mode: str = "not_configured"  # not_configured (по умолчанию) | fake
    # Отчёты для FakeBkiProvider по сценарию — JSON {"Фамилия|Имя|ГГГГ-ММ-ДД": {...}}
    # в формате app.sources.bki.serialize_report. Пусто -> пустой FakeBkiProvider
    # (как раньше), см. app/sources/factory.py. Для scripts/e2e_check.py.
    bki_fixture_path: str = ""

    # Админка (T12) — технические параметры аутентификации/сессий, не бизнес-
    # правила (правило 2 CLAUDE.md касается порогов заявки, не защиты входа).
    # T13: фронтенд и API — один origin (Vite proxy в dev, nginx в бою отдаёт
    # статику и проксирует /api) — CORS не нужен, браузер всегда обращается к
    # тому же адресу, кука работает без каких-либо межсайтовых настроек.
    #
    # cookie_secure — дефолт строгий (True), как и APP_ENV: забытая настройка
    # не должна тихо снять Secure с продовой куки. В dev его выставляют явно в
    # false (docker-compose.yml) — Secure-кука на http://localhost ведёт себя
    # по-разному в разных браузерах (Safari может не сохранить её вовсе, а
    # Chrome/Firefox считают localhost доверенным источником и сохраняют) —
    # без этого вход в Safari при локальной разработке тихо не срабатывает.
    # В production сервис отказывается стартовать с cookie_secure=false
    # (app/startup_guard.py) — тем же способом, что и с фейковыми провайдерами.
    cookie_secure: bool = True
    session_ttl_hours: int = 12
    session_idle_timeout_minutes: int = 30
    # Блокировка по учётной записи — короткая (email уже известен, это не защита
    # от подбора самого email); по IP — отдельно и независимо от конкретного
    # email, иначе кто угодно, зная чужой email, держит его заблокированным
    # бесконечно, просто продолжая слать неверные пароли.
    login_account_max_failed_attempts: int = 5
    login_account_lockout_minutes: int = 5
    login_ip_max_failed_attempts: int = 20
    login_ip_lockout_window_minutes: int = 15
    # CSRF: все изменяющие запросы (не GET/HEAD/OPTIONS) обязаны нести этот
    # заголовок — простая форма/кросс-сайтовая отправка не может его выставить,
    # а CORS не пускает произвольный JS-код с чужого origin ставить его тоже.
    csrf_header_name: str = "X-Requested-With"


@lru_cache
def get_settings() -> Settings:
    return Settings()
