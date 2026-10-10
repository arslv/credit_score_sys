#!/bin/sh
# T19: "проверка восстановления на тестовой базе" (ваше явное указание) — НЕ
# трогает боевую базу. Восстанавливает дамп во временную базу
# dialog_restore_check, проверяет пару ожидаемых таблиц, удаляет временную
# базу. Запускать внутри сервиса `backup`:
#   docker compose -f docker-compose.prod.yml exec backup sh /scripts/verify_backup_restore.sh
# (без аргумента — берёт самый свежий файл из BACKUP_DIR; можно явно указать
# конкретный дамп первым аргументом).
#
# Это ручная/плановая проверка (например, раз в неделю по cron на хосте,
# см. docs/deployment.md), не часть автоматического алертинга — алерт в
# app/tasks/alerts.py проверяет только ФАКТ, что дамп СДЕЛАН (маркер), не то,
# что он восстанавливается; это разные гарантии.
set -eu

: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"
: "${BACKUP_DIR:=/backups}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD не задан}"

dump_file="${1:-}"
if [ -z "$dump_file" ]; then
    dump_file=$(find "$BACKUP_DIR" -name 'dialog-scoring-*.dump' | sort | tail -n1)
fi
if [ -z "$dump_file" ] || [ ! -f "$dump_file" ]; then
    echo "[verify] дамп не найден в ${BACKUP_DIR}" >&2
    exit 1
fi

check_db="dialog_restore_check"
echo "[verify] восстановление ${dump_file} во временную базу ${check_db}"

dropdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" --if-exists "$check_db"
createdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$check_db"
pg_restore -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$check_db" "$dump_file"

applications_count=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$check_db" -tAc "select count(*) from applications;")
alembic_version=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$check_db" -tAc "select version_num from alembic_version;")

echo "[verify] восстановлено: applications=${applications_count}, alembic_version=${alembic_version}"

dropdb -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" "$check_db"

echo "[verify] OK — дамп восстанавливается и содержит ожидаемые таблицы (временная база удалена)"
