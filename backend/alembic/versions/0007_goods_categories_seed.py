"""goods_categories: code column + starter seed (T10)

Стартовый список и признаки sharia_allowed/liquidity/lgd_estimate —
ИЛЛЮСТРАТИВНЫЕ: разрешённые категории взяты из типичных товаров murabaha уже
встречающихся в тестах (бытовая техника и т.п.), запрещённые — прямо из ТЗ
(«Ограничения, вытекающие из шариата»: алкоголь, табак, свинина, азартные
игры). LGD-оценки — не откалиброванные, просто правдоподобные по ликвидности.
ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ шариатским контролем заказчика перед продакшеном — см.
docs/spec.md, «Вопросы, не блокирующие старт» и «Решения, требующие
подтверждения заказчика».

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED = [
    # code, name, sharia_allowed, liquidity, lgd_estimate
    ("household_appliances", "Бытовая техника", True, "high", "0.300"),
    ("electronics", "Электроника", True, "high", "0.250"),
    ("furniture", "Мебель", True, "medium", "0.450"),
    ("clothing_footwear", "Одежда и обувь", True, "low", "0.700"),
    ("alcohol", "Алкогольная продукция", False, "low", None),
    ("tobacco", "Табачная продукция", False, "low", None),
    ("pork_products", "Продукция из свинины", False, "low", None),
    ("gambling", "Товары и услуги для азартных игр", False, "low", None),
]

goods_categories = sa.table(
    "goods_categories",
    sa.column("code", sa.Text),
    sa.column("name", sa.Text),
    sa.column("sharia_allowed", sa.Boolean),
    sa.column("liquidity", sa.Text),
    sa.column("lgd_estimate", sa.Numeric),
)


def upgrade() -> None:
    op.add_column("goods_categories", sa.Column("code", sa.Text(), nullable=True))
    op.execute("UPDATE goods_categories SET code = lower(replace(name, ' ', '_'))")
    op.alter_column("goods_categories", "code", nullable=False)
    op.create_unique_constraint("uq_goods_categories_code", "goods_categories", ["code"])

    op.bulk_insert(
        goods_categories,
        [
            {
                "code": code,
                "name": name,
                "sharia_allowed": sharia_allowed,
                "liquidity": liquidity,
                "lgd_estimate": lgd_estimate,
            }
            for code, name, sharia_allowed, liquidity, lgd_estimate in _SEED
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM goods_categories WHERE code IN ({})".format(
            ", ".join(f"'{code}'" for code, *_ in _SEED)
        )
    )
    op.drop_constraint("uq_goods_categories_code", "goods_categories", type_="unique")
    op.drop_column("goods_categories", "code")
