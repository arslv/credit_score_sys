"""Источник файла реестра.

Проверено по фактам из этого окружения (T08, "Разберись, можно ли
автоматизировать скачивание реестров"):

- mvd.ru — недоступен на уровне сети: TCP-соединение устанавливается (порты 80 и
  443), но дальше — тишина: ни TLS ServerHello, ни HTTP-ответа, таймаут через
  30 с. Это не капча и не JS-защита (до них дело не доходит) — похоже на
  фильтрацию пакетов после установления соединения. Не удалось определить,
  на чьей стороне блокировка (egress этого окружения или сама mvd.ru глушит
  трафик с иностранных/датацентровых адресов) — с уверенностью можно сказать
  только то, что обычный HTTP-запрос отсюда не проходит вообще.
- fedsfm.ru — доступен: TCP и TLS отрабатывают полностью, ответ приходит за
  0.5 с. Это настоящий HTTP 403 Forbidden от их edge/WAF (тело — "Request ID:
  ..." в характерном для WAF формате), а не блокировка окружения: соединение
  реально дошло и им ответили отказом. Дополнительно их сертификат не проходит
  проверку через стандартный набор корневых CA ("unable to get local issuer
  certificate") — отдельная, тоже настоящая техническая деталь, а не следствие
  блокировки.

В обоих случаях до страницы с ссылкой на файл выгрузки дойти не удалось, поэтому
конкретные рабочие URL ниже НЕ подставлены — угадывать их не стал. HttpDownloader
собран как рабочий, протестированный (respx) механизм: URL — только из конфига,
с поддержкой strftime-плейсхолдеров на случай даты в пути. Как только появится
реальный доступ (другая сеть или документация от заказчика/Росфинмониторинга) —
достаточно проставить MVD_REGISTRY_URL/ROSFINMON_REGISTRY_URL и переключить
REGISTRIES_DOWNLOADER=http, менять код не придётся.

LocalFileDownloader остаётся основным вариантом на сейчас и для тестов."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx


class RegistryDownloader(Protocol):
    async def download(self, registry: str) -> bytes: ...


class LocalFileDownloader:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    async def download(self, registry: str) -> bytes:
        path = self._base_dir / f"{registry}.csv"
        return await asyncio.to_thread(path.read_bytes)


class HttpDownloader:
    """Скачивает файл реестра по HTTP(S). url_templates — {имя_реестра: URL},
    URL может содержать strftime-плейсхолдеры (%Y, %m, %d, ...) для источников,
    публикующих файл с датой в пути или имени."""

    def __init__(self, url_templates: dict[str, str], *, timeout: float = 30.0) -> None:
        self._url_templates = url_templates
        self._timeout = timeout

    def _resolve_url(self, registry: str) -> str:
        try:
            template = self._url_templates[registry]
        except KeyError as exc:
            raise ValueError(f"нет URL для реестра {registry!r} в конфиге") from exc
        if not template:
            raise ValueError(f"URL для реестра {registry!r} не настроен (пустая строка)")
        return datetime.now(timezone.utc).strftime(template)

    async def download(self, registry: str) -> bytes:
        url = self._resolve_url(registry)
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
