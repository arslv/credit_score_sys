from decimal import Decimal

from app.engine.goods import compute_goods_features, find_goods_category
from app.models import GoodsCategory


async def _add_category(session, **overrides) -> GoodsCategory:
    # code/name с префиксом __test_ — не пересекается с сидом миграции 0007
    # (household_appliances/electronics/... уже занята реальными данными).
    defaults = dict(code="__test_category__", name="__Тестовая категория__", sharia_allowed=True, liquidity="high")
    defaults.update(overrides)
    category = GoodsCategory(**defaults)
    session.add(category)
    await session.flush()
    return category


async def test_find_goods_category_by_code(db_session):
    await _add_category(db_session, code="__test_category__", name="__Найденная категория__")
    category = await find_goods_category(db_session, "__test_category__")
    assert category is not None
    assert category.name == "__Найденная категория__"


async def test_find_goods_category_missing_code_returns_none(db_session):
    assert await find_goods_category(db_session, "no_such_code") is None


async def test_compute_goods_features_no_code_at_all(db_session):
    features = await compute_goods_features(db_session, None)
    assert features == {"goods": {"category_code": None, "category_found": False}}


async def test_compute_goods_features_unknown_code_is_not_found_not_error(db_session):
    features = await compute_goods_features(db_session, "does_not_exist")
    assert features["goods"]["category_found"] is False
    assert "sharia_allowed" not in features["goods"]


async def test_compute_goods_features_known_allowed_category(db_session):
    await _add_category(db_session, sharia_allowed=True, liquidity="high", lgd_estimate=Decimal("0.250"))
    features = await compute_goods_features(db_session, "__test_category__")
    assert features["goods"]["category_found"] is True
    assert features["goods"]["sharia_allowed"] is True
    assert features["goods"]["liquidity"] == "high"
    assert features["goods"]["lgd_estimate"] == 0.25
