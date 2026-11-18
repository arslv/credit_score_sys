"""goods_categories: список категорий CRM (решение владельца 18.11.2026)

Категории — ровно те, что в форме CRM (lk.dialog-fp.ru), в их порядке:
Бытовая техника, Телевизоры, Мягкая мебель, Кухонная мебель, Телефоны,
Автомобиль, Иное, Заказной товар. Прежние стартовые категории (T10:
электроника, мебель, одежда, запрещённые шариатом) — выключены (active=false),
не удалены: на них ссылаются уже поданные заявки. Порядок в списке — новая
колонка sort_order.

Ликвидность (high/medium/low) — от неё зависит правило матрицы
«goods_liquidity: low → порог одобрения 760 и взнос 20%» (config/rulesets):
ходовой товар — high, мебель — medium, «Иное» и «Заказной товар» (под
заказ, перепродать трудно) — low. Подтвердить у владельца.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# code, name, liquidity — в порядке формы CRM.
_CRM_CATEGORIES = [
    ("household_appliances", "Бытовая техника", "high"),
    ("tv", "Телевизоры", "high"),
    ("upholstered_furniture", "Мягкая мебель", "medium"),
    ("kitchen_furniture", "Кухонная мебель", "medium"),
    ("phones", "Телефоны", "high"),
    ("car", "Автомобиль", "medium"),
    ("other", "Иное", "low"),
    ("custom_order", "Заказной товар", "low"),
]


def upgrade() -> None:
    op.add_column("goods_categories", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.execute("UPDATE goods_categories SET active = false")

    bind = op.get_bind()
    for position, (code, name, liquidity) in enumerate(_CRM_CATEGORIES, start=1):
        existing = bind.execute(
            sa.text("SELECT id FROM goods_categories WHERE code = :code OR name = :name"),
            {"code": code, "name": name},
        ).first()
        if existing is not None:
            bind.execute(
                sa.text(
                    "UPDATE goods_categories SET code = :code, name = :name, liquidity = :liquidity, "
                    "sharia_allowed = true, active = true, sort_order = :position WHERE id = :id"
                ),
                {"code": code, "name": name, "liquidity": liquidity, "position": position, "id": existing.id},
            )
        else:
            bind.execute(
                sa.text(
                    "INSERT INTO goods_categories (code, name, sharia_allowed, liquidity, active, sort_order) "
                    "VALUES (:code, :name, true, :liquidity, true, :position)"
                ),
                {"code": code, "name": name, "liquidity": liquidity, "position": position},
            )


def downgrade() -> None:
    codes = [code for code, _name, _liquidity in _CRM_CATEGORIES if code != "household_appliances"]
    op.execute(
        "DELETE FROM goods_categories WHERE code IN ({}) AND id NOT IN "
        "(SELECT goods_category_id FROM applications WHERE goods_category_id IS NOT NULL)".format(
            ", ".join(f"'{code}'" for code in codes)
        )
    )
    op.execute("UPDATE goods_categories SET active = true")
    op.drop_column("goods_categories", "sort_order")
