from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_service_account(authorization: str | None = Header(default=None)) -> None:
    """Имитирует проверку служебного аккаунта интеграции в реальной системе заказчика."""
    settings = get_settings()
    expected = f"Bearer {settings.fake_partner_token}"

    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing token")
