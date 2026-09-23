FROM python:3.12-slim

WORKDIR /app

# fonts-dejavu-core даёт кириллицу для синтетических картинок паспорта
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY tests ./tests

RUN pip install --no-cache-dir ".[dev]"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
