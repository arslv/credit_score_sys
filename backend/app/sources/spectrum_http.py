"""HTTP-транспорт Спектрум Даты — асинхронный B2B-API
(https://b2b-api.spectrumdata.ru/swagger-ui/index.html):

- POST /b2b/api/v1/user/reports/{report_type_uid}/_make — заказать отчёт
  (тело {queryType: MULTIPART, query: " ", data: {...}}), в ответе uid отчёта;
- GET /b2b/api/v1/user/reports/{uid}?_content=true&_detailed=true — забрать
  его; готов, когда progress_wait == 0;
- GET /b2b/api/v1/user и /user/report_types — проверка доступа и список
  пакетов (app/cli/spectrum.py).

Авторизация — заголовок «AR-REST base64(user:stamp:age:salted)», где
salted = base64(md5(stamp:age:base64(md5(password)))) (схема devToken в
Swagger). Логин и пароль — только из окружения (SPECTRUM_USER/PASSWORD).

Защита от двойного списания: uid заказанного отчёта запоминается в Redis
(ключ — пакет и хеш запроса) до тех пор, пока отчёт не забран; если отчёт
не успел сформироваться за время ожидания, следующая попытка по той же
заявке забирает тот же отчёт, а не заказывает новый."""

import asyncio
import base64
import hashlib
import json
import time
from typing import Any, Protocol

import httpx

_API = "/b2b/api/v1"
_PENDING_TTL_SECONDS = 24 * 3600


class SpectrumApiError(Exception):
    pass


class PendingStore(Protocol):
    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    async def delete(self, *keys: str) -> Any: ...


def _b64_md5(text: str) -> str:
    return base64.b64encode(hashlib.md5(text.encode("utf-8")).digest()).decode("ascii")


def auth_header(user: str, password: str, *, stamp: int, age: int) -> str:
    salted = _b64_md5(f"{stamp}:{age}:{_b64_md5(password)}")
    token = base64.b64encode(f"{user}:{stamp}:{age}:{salted}".encode("utf-8")).decode("ascii")
    return f"AR-REST {token}"


def _error_text(response: httpx.Response) -> str:
    """Код и название ошибки из тела (content_403/404 в Swagger) — без
    персданных: тело запроса туда не попадает."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and (body.get("code") or body.get("name")):
        return f"HTTP {response.status_code} {body.get('code', '')} {body.get('name', '')}".strip()
    return f"HTTP {response.status_code}"


def _pending_key(report_type_uid: str, data: dict[str, str]) -> str:
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"spectrum:pending:{report_type_uid}:{digest}"


class SpectrumHttpTransport:
    def __init__(
        self,
        *,
        base_url: str,
        user: str,
        password: str,
        pending: PendingStore,
        token_age_seconds: int = 300,
        request_timeout_seconds: float = 20.0,
        poll_interval_seconds: float = 2.0,
        poll_timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user = user
        self._password = password
        self._pending = pending
        self._token_age = token_age_seconds
        self._timeout = request_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._poll_timeout = poll_timeout_seconds
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": auth_header(self._user, self._password, stamp=int(time.time()), age=self._token_age),
            "Accept": "application/json",
        }

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
        response = await client.get(f"{_API}{path}", params=params, headers=self._headers())
        if response.status_code != 200:
            raise SpectrumApiError(_error_text(response))
        return response.json()

    async def user_info(self) -> dict:
        async with self._client() as client:
            return await self._get(client, "/user")

    async def report_types(self) -> list[dict]:
        async with self._client() as client:
            body = await self._get(client, "/user/report_types", {"_can_generate": "true", "_size": 200})
        return body.get("data") or []

    async def _make(self, client: httpx.AsyncClient, report_type_uid: str, data: dict[str, str]) -> str:
        response = await client.post(
            f"{_API}/user/reports/{report_type_uid}/_make",
            json={"queryType": "MULTIPART", "query": " ", "data": data},
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise SpectrumApiError(_error_text(response))
        items = response.json().get("data") or []
        uid = items[0].get("uid") if items and isinstance(items[0], dict) else None
        if not uid:
            raise SpectrumApiError("в ответе на заказ нет uid отчёта")
        return uid

    async def fetch_report(self, report_type_uid: str, data: dict[str, str]) -> dict:
        key = _pending_key(report_type_uid, data)
        async with self._client() as client:
            stored = await self._pending.get(key)
            if stored:
                uid = stored.decode() if isinstance(stored, bytes) else str(stored)
            else:
                uid = await self._make(client, report_type_uid, data)
                await self._pending.set(key, uid, ex=_PENDING_TTL_SECONDS)

            deadline = time.monotonic() + self._poll_timeout
            while True:
                response = await client.get(
                    f"{_API}/user/reports/{uid}",
                    params={"_content": "true", "_detailed": "true"},
                    headers=self._headers(),
                )
                if response.status_code == 404:
                    await self._pending.delete(key)  # отчёта больше нет — в следующий раз закажем заново
                    raise SpectrumApiError(_error_text(response))
                if response.status_code != 200:
                    raise SpectrumApiError(_error_text(response))
                body = response.json()
                items = body.get("data") or []
                if items and isinstance(items[0], dict) and items[0].get("progress_wait", 0) == 0:
                    await self._pending.delete(key)
                    return body
                if time.monotonic() >= deadline:
                    # uid остаётся в Redis — повтор заберёт этот же отчёт.
                    raise SpectrumApiError("отчёт ещё формируется, повторите позже")
                await asyncio.sleep(self._poll_interval)
