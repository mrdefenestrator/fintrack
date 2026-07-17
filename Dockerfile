FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv ./.venv

COPY fintrack/ ./fintrack/
COPY web/ ./web/
COPY configs/ ./configs/
COPY migrations/ ./migrations/
COPY fintrack.py ./
COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 5003

ENV FINTRACK_DB=/app/data/fintrack.db

CMD ["./docker-entrypoint.sh"]
