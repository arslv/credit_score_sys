import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    # status может быть "degraded", если другие тесты в этой сессии уже создали
    # активный ruleset (реальными коммитами) без синхронизации реестров — это не
    # авария БД/Redis, а честная деградация по свежести реестров (T08).
    assert body["status"] in ("ok", "degraded")
    assert body["checks"] == {"database": "ok", "redis": "ok"}
