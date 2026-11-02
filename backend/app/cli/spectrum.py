"""Проверка доступа к Спектрум Дате и список пакетов — ничего не списывает.

    docker compose exec api python -m app.cli.spectrum check
        войти в API (SPECTRUM_USER / SPECTRUM_PASSWORD из .env) и показать,
        под каким пользователем

    docker compose exec api python -m app.cli.spectrum report-types
        пакеты, которые этот пользователь может заказывать: их uid — это
        bki.report_types.*.provider_code в правилах

Работает независимо от BKI_PROVIDER_MODE — чтобы проверить доступ, прежде
чем включать. Пароль не печатается."""

import argparse
import asyncio
import sys

from app.config import get_settings
from app.sources.factory import build_spectrum_transport


async def _main(command: str) -> int:
    settings = get_settings()
    if not (settings.spectrum_user and settings.spectrum_password):
        print("Не заданы SPECTRUM_USER и SPECTRUM_PASSWORD в .env.")
        return 1
    transport = build_spectrum_transport(settings)
    try:
        if command == "check":
            users = (await transport.user_info()).get("data") or [{}]
            user = users[0]
            print(f"Доступ есть: пользователь {user.get('login') or user.get('uid')}, "
                  f"домен {user.get('domain_uid')}, статус {user.get('state')}.")
        else:
            types = await transport.report_types()
            if not types:
                print("Пакетов, доступных для заказа, нет.")
            for item in types:
                print(f"{item.get('uid')}  —  {item.get('name')}  ({item.get('state')})")
    except Exception as exc:
        print(f"Не удалось: {exc}")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "report-types"])
    sys.exit(asyncio.run(_main(parser.parse_args().command)))


if __name__ == "__main__":
    main()
