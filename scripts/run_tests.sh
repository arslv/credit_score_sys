#!/bin/sh
# T22: общий прогон pytest — бэкенд и заглушка заказчика (fake_partner).
# Раньше тесты fake_partner не входили ни в один прогон и тихо устарели
# после T18 (17 ошибок). Падает, если упал хоть один набор.
# Playwright (frontend: npm run test:e2e) и scripts/e2e_check.py — отдельно.
set -e
cd "$(dirname "$0")/.."

echo "=== backend (api) ==="
docker compose exec -T api pytest -q "$@"

echo "=== fake_partner ==="
docker compose exec -T fake_partner pytest -q
