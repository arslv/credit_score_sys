"""Категория товара по коду из системы заказчика (T10, ТЗ «Ограничения,
вытекающие из шариата» + справочник goods_categories). Код без совпадения в
справочнике — не отказ, флаг оператору: справочник может отставать от их
каталога, это не повод ронять заявку."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GoodsCategory


async def find_goods_category(session: AsyncSession, code: str) -> GoodsCategory | None:
    return (
        await session.execute(select(GoodsCategory).where(GoodsCategory.code == code))
    ).scalar_one_or_none()


async def compute_goods_features(session: AsyncSession, goods_category_code: str | None) -> dict:
    """features["goods"] — неймспейс, как features["checks"]/features["features"]
    (см. app/engine/pipeline.py). sharia_allowed отсутствует в словаре, если код
    не найден или не передан — hard_stop sharia_goods (dev.yaml) op=eq
    сработает только на явном False, отсутствие поля не триггерит его (это и
    есть "флаг оператору, не отказ": ход решает stage_b_completeness/ручной
    разбор, а не автоматический reject)."""
    if not goods_category_code:
        return {"goods": {"category_code": None, "category_found": False}}

    category = await find_goods_category(session, goods_category_code)
    if category is None:
        return {"goods": {"category_code": goods_category_code, "category_found": False}}

    return {
        "goods": {
            "category_code": goods_category_code,
            "category_found": True,
            "sharia_allowed": category.sharia_allowed,
            "liquidity": category.liquidity,
            "lgd_estimate": float(category.lgd_estimate) if category.lgd_estimate is not None else None,
        }
    }
