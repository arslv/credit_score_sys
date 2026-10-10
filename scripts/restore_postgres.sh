#!/bin/sh
# T19: восстановление Postgres из дампа, сделанного backup_postgres.sh.
# Запускать внутри сервиса `backup` (тот же образ postgres:16, видит ту же
# сеть/БД): `docker compose -f docker-compose.prod.yml exec backup
# sh /scripts/restore_postgres.sh /backups/dialog-scoring-XXXXXXXXTXXXXXXZ.dump`
#
# ВНИМАНИЕ: перезаписывает целевую базу (--clean --if-exists). По умолчанию
# целится в POSTGRES_DB (боевую) — для проверки на отдельной тестовой базе
# используйте scripts/verify_backup_restore.sh, не этот скрипт.
set -eu

: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"

if [ "$#" -lt 1 ]; then
    echo "Использование: $0 <путь-к-дампу> [имя_базы]" >&2
    exit 1
fi

dump_file="$1"
target_db="${2:-$POSTGRES_DB}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD не задан}"

if [ ! -f "$dump_file" ]; then
    echo "Файл дампа не найден: $dump_file" >&2
    exit 1
fi

echo "[restore] ВНИМАНИЕ: это перезапишет базу '${target_db}' на хосте ${POSTGRES_HOST} содержимым ${dump_file}."
echo "[restore] Нажмите Ctrl+C в течение 5 секунд, чтобы отменить."
sleep 5

pg_restore -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$target_db" --clean --if-exists "$dump_file"

echo "[restore] готово: ${target_db} восстановлена из ${dump_file}"
