#!/bin/sh
# T19: ежедневный бэкап Postgres. Запускается внутри сервиса `backup`
# (docker-compose.prod.yml, образ postgres:16 — в нём уже есть pg_dump).
#
# Режимы:
#   sh backup_postgres.sh        — один дамп и выход (для cron на хосте, если
#                                   кто-то предпочитает его сервису `backup`)
#   sh backup_postgres.sh loop   — то же самое раз в BACKUP_INTERVAL_SECONDS,
#                                   бесконечно (так его запускает сервис `backup`)
#
# После успешного дампа обновляет mtime BACKUP_MARKER_PATH — это и есть сигнал
# "бэкап сделан" для алертов (app/tasks/alerts.py, T19); при ошибке (`set -e`)
# маркер не трогается, и алерт сработает по возрасту старого маркера.
set -eu

: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"
: "${BACKUP_DIR:=/backups}"
: "${BACKUP_RETENTION_DAYS:=14}"
: "${BACKUP_MARKER_PATH:=/backups/.last_success}"
: "${BACKUP_INTERVAL_SECONDS:=86400}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD не задан}"

run_once() {
    mkdir -p "$BACKUP_DIR"
    timestamp=$(date -u +%Y%m%dT%H%M%SZ)
    dump_file="$BACKUP_DIR/dialog-scoring-${timestamp}.dump"

    echo "[backup] дамп базы ${POSTGRES_DB} -> ${dump_file}"
    pg_dump -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$dump_file"

    touch "$BACKUP_MARKER_PATH"

    echo "[backup] удаление дампов старше ${BACKUP_RETENTION_DAYS} дней"
    find "$BACKUP_DIR" -name 'dialog-scoring-*.dump' -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete

    kept=$(find "$BACKUP_DIR" -name 'dialog-scoring-*.dump' | wc -l)
    echo "[backup] готово: хранится файлов — ${kept}"
}

if [ "${1:-}" = "loop" ]; then
    while true; do
        run_once || echo "[backup] дамп НЕ УДАЛСЯ — маркер не обновлён, см. вывод выше" >&2
        sleep "$BACKUP_INTERVAL_SECONDS"
    done
else
    run_once
fi
