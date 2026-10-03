"""T15: представления целевой переменной (ТЗ «Целевая переменная»):
target_bad_60_6 (просрочка 60+ за 6 мес. — укороченный вариант для молодого
портфеля) и target_bad_90_12 (базовое определение, 90+ за 12 мес.).

Зрелость — от даты данных, а не от сегодня (ваше явное указание, T15 п.5):
окно наблюдения ограничено последней датой, по которую вообще есть платёжные
данные (MAX(import_batches.data_as_of) по завершённым пакетам kind=payments),
не текущим днём. Таргет известен в четырёх случаях, во всех остальных — NULL:
  1) плохой исход (DPD >= порог) уже наступил внутри окна — дальше ждать
     незачем, bad=1, независимо от того, покрыто ли окно целиком и как
     закрылся договор (жёсткая улика по факту просрочки важнее трактовки
     причины закрытия);
  2) договор закрыт до конца окна погашением (closure_outcome='good') —
     дальше риска нет, bad=0;
  3) договор закрыт до конца окна изъятием товара или списанием
     (closure_outcome='bad') — это дефолт, а не хороший исход, bad=1, даже
     если 60+/90+ дней просрочки по графику формально не набралось (ваше
     замечание после первой версии: «закрытый договор — не всегда хороший»;
     трактовка причины закрытия — config/import_mappings/contracts.yaml,
     не код, см. app/engine/imports.py и app/models/contract.py::CLOSURE_OUTCOMES);
  4) окно наблюдения полностью покрыто данными (data_as_of >= конец окна) и
     договор не закрыт (или закрыт по нераспознанной причине раньше этого) —
     bad=0 по факту отсутствия просрочки на конец окна.
Закрытый договор с нераспознанной причиной (closure_outcome='unknown')
не попадает ни в (2), ни в (3) — таргет остаётся NULL, пока окно не
покрыто данными (ваше явное указание: «неизвестная причина закрытия —
NULL, а не 0»).
Решение оформлено представлением, не хранится в таблице (ТЗ: «определение
таргета будет меняться») — пересчитывается на каждый SELECT из текущих
contracts/delinquency_daily/import_batches.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-02

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _view_sql(name: str, months: int, threshold: int) -> str:
    return f"""
    CREATE VIEW {name} AS
    WITH data_horizon AS (
        SELECT MAX(data_as_of) AS as_of
        FROM import_batches
        WHERE kind = 'payments' AND status = 'completed'
    ),
    windows AS (
        SELECT
            c.id AS contract_id,
            c.closed_at,
            c.closure_outcome,
            (c.issued_at + interval '{months} months')::date AS window_end,
            dh.as_of AS data_as_of
        FROM contracts c
        CROSS JOIN data_horizon dh
    ),
    worst AS (
        SELECT
            w.contract_id,
            w.closed_at,
            w.closure_outcome,
            w.window_end,
            w.data_as_of,
            COALESCE(
                MAX(d.days_past_due) FILTER (
                    WHERE w.data_as_of IS NOT NULL
                      AND d.as_of <= LEAST(w.window_end, w.data_as_of)
                ),
                0
            ) AS max_dpd_in_window
        FROM windows w
        LEFT JOIN delinquency_daily d ON d.contract_id = w.contract_id
        GROUP BY w.contract_id, w.closed_at, w.closure_outcome, w.window_end, w.data_as_of
    )
    SELECT
        contract_id,
        CASE
            WHEN max_dpd_in_window >= {threshold} THEN 1
            WHEN closed_at IS NOT NULL AND closed_at <= window_end AND closure_outcome = 'good' THEN 0
            WHEN closed_at IS NOT NULL AND closed_at <= window_end AND closure_outcome = 'bad' THEN 1
            WHEN closed_at IS NOT NULL AND closed_at <= window_end AND closure_outcome = 'unknown' THEN NULL
            WHEN data_as_of IS NOT NULL AND data_as_of >= window_end THEN 0
            ELSE NULL
        END AS bad,
        (
            max_dpd_in_window >= {threshold}
            OR (closed_at IS NOT NULL AND closed_at <= window_end AND closure_outcome IN ('good', 'bad'))
            OR (
                data_as_of IS NOT NULL AND data_as_of >= window_end
                -- договор закрыт по нераспознанной причине раньше конца окна -> bad
                -- навсегда NULL (ваше явное указание), значит и "зрелым" его
                -- появление новых данных горизонта не делает.
                AND NOT (closed_at IS NOT NULL AND closed_at <= window_end AND closure_outcome = 'unknown')
            )
        ) AS is_mature
    FROM worst
    """


def upgrade() -> None:
    op.execute(_view_sql("target_bad_60_6", months=6, threshold=60))
    op.execute(_view_sql("target_bad_90_12", months=12, threshold=90))


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS target_bad_90_12")
    op.execute("DROP VIEW IF EXISTS target_bad_60_6")
