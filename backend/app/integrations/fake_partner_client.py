from datetime import datetime
from decimal import Decimal

import httpx

from app.integrations.base import (
    PartnerApplicationDetail,
    PartnerApplicationSummary,
    PartnerBadRequestError,
    PartnerConflictError,
    PartnerUnavailableError,
)


def _to_detail(payload: dict) -> PartnerApplicationDetail:
    form = payload["form"]
    technical = payload["technical"]
    return PartnerApplicationDetail(
        external_id=payload["id"],
        status=payload["status"],
        created_at=datetime.fromisoformat(payload["created_at"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
        last_name=form["last_name"],
        first_name=form["first_name"],
        middle_name=form.get("middle_name"),
        phone=form["phone"],
        goods_name=form["goods_name"],
        merchant_name=form.get("merchant_name"),
        merchant_city=form.get("merchant_city"),
        goods_price=Decimal(str(form["goods_price"])) if form.get("goods_price") is not None else None,
        down_payment=Decimal(str(form["down_payment"])) if form.get("down_payment") is not None else None,
        term_months=form.get("term_months"),
        tariff=form.get("tariff"),
        consent_pd_at=datetime.fromisoformat(form["consent_pd"]["given_at"]),
        consent_bki_at=(
            datetime.fromisoformat(form["consent_bki"]["given_at"]) if form.get("consent_bki") else None
        ),
        technical_ip=technical.get("ip"),
        technical_user_agent=technical.get("user_agent"),
        photo_kinds=list(payload.get("photos", [])),
        raw=payload,
    )


class FakePartnerClient:
    """Реализация PartnerClient поверх сервиса fake_partner (T03).

    Каждый метод делает ОДНУ попытку и поднимает исключение при неудаче —
    повторы и логирование в integration_events на уровне адаптера
    (app/integrations/adapter.py), а не здесь.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PartnerUnavailableError(str(exc)) from exc

        if response.status_code >= 500:
            raise PartnerUnavailableError(f"{method} {url} -> {response.status_code}")
        return response

    async def list_applications(
        self, status: str, since: datetime | None = None
    ) -> list[PartnerApplicationSummary]:
        params = {"status_": status}
        if since is not None:
            params["since"] = since.isoformat()

        response = await self._request("GET", "/applications", params=params)
        response.raise_for_status()
        return [
            PartnerApplicationSummary(
                external_id=item["id"],
                status=item["status"],
                updated_at=datetime.fromisoformat(item["updated_at"]),
            )
            for item in response.json()
        ]

    async def get_application(self, external_id: str) -> PartnerApplicationDetail:
        response = await self._request("GET", f"/applications/{external_id}")
        response.raise_for_status()
        return _to_detail(response.json())

    async def download_photo(self, external_id: str, kind: str) -> bytes:
        response = await self._request("GET", f"/applications/{external_id}/photos/{kind}")
        response.raise_for_status()
        return response.content

    async def write_status(
        self,
        external_id: str,
        expected_current_status: str,
        new_status: str,
        comment: str | None = None,
    ) -> None:
        current = await self.get_application(external_id)
        if current.status != expected_current_status:
            raise PartnerConflictError(expected=expected_current_status, actual=current.status)

        response = await self._request(
            "POST",
            f"/applications/{external_id}/status",
            json={"status": new_status, "comment": comment},
        )
        if response.status_code == 400:
            raise PartnerBadRequestError(response.text)
        if response.status_code == 409:
            raise PartnerConflictError(expected=expected_current_status, actual=new_status)
        response.raise_for_status()

    async def add_comment(self, external_id: str, text: str) -> None:
        response = await self._request(
            "POST", f"/applications/{external_id}/comments", json={"text": text}
        )
        response.raise_for_status()
