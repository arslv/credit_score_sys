"""Категории товаров — список CRM (решение владельца 18.11.2026, миграция 0027)."""

from app.engine.goods import find_goods_category
from tests._admin_helpers import create_employee, login

CRM_ORDER = [
    "Бытовая техника", "Телевизоры", "Мягкая мебель", "Кухонная мебель",
    "Телефоны", "Автомобиль", "Иное", "Заказной товар",
]


async def test_manual_form_offers_exactly_crm_categories_in_order(admin_client, session_factory):
    await create_employee(session_factory, email="categories@example.com", role="operator")
    await login(admin_client, "categories@example.com")
    response = await admin_client.get("/api/manual-applications/options")
    assert response.status_code == 200, response.text
    names = [c["name"] for c in response.json()["goods_categories"]]
    # Сначала — список CRM в её порядке (другие тесты могут добавить свои
    # категории в общую тестовую базу — они идут после); прежних стартовых нет.
    assert names[: len(CRM_ORDER)] == CRM_ORDER
    for old in ("Электроника", "Мебель", "Одежда и обувь", "Алкогольная продукция"):
        assert old not in names


async def test_category_found_by_code_and_by_crm_label(session_factory):
    async with session_factory() as session:
        by_code = await find_goods_category(session, "tv")
        by_label = await find_goods_category(session, "телевизоры")
        assert by_code is not None and by_label is not None
        assert by_code.id == by_label.id
        assert await find_goods_category(session, "несуществующая") is None
