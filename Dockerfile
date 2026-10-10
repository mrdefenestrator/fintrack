# Front-end assets (Tailwind CSS + vendored JS). Output is plain CSS/JS that's
# identical on every architecture, so build it once on the native build
# platform instead of under QEMU emulation for each target arch. The npm ci
# layer is only rebuilt when package*.json change.
FROM --platform=$BUILDPLATFORM node:22-slim AS assets

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

COPY tailwind.config.js ./
COPY scripts/copy-vendor.mjs ./scripts/
COPY web/ ./web/
RUN npm run build


FROM python:3.14-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev


FROM python:3.14-slim

WORKDIR /app

COPY --from=builder /app/.venv ./.venv

COPY fintrack/ ./fintrack/
COPY web/ ./web/
COPY --from=assets /app/web/static/dist/ ./web/static/dist/
COPY configs/ ./configs/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY fintrack.py ./
COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 5003

ENV FINTRACK_DB=/app/data/fintrack.db

CMD ["./docker-entrypoint.sh"]
