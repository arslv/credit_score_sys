FROM python:3.12-slim

# Явно, не понадеявшись на то, что base-образ сегодня сам ставит C.UTF-8 —
# нашёлся баг от обратного (app/cli/create_admin.py): без гарантированной
# UTF-8-локали stdin на POSIX всё равно не упал бы сам (surrogateescape,
# PEP 528), но ФИО с битой кодировкой долетало до asyncpg и роняло команду
# трассировкой через SQLAlchemy далеко от места ввода. Основная защита —
# в самом create_admin.py (проверка до записи в БД); это — чтобы терминал/
# docker exec по умолчанию был в UTF-8, а не ASCII/POSIX.
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests

RUN pip install --no-cache-dir ".[dev]"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
