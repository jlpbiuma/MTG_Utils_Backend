FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl \
    curl \
    ca-certificates \
    libatomic1 \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY prisma ./prisma
RUN uv run prisma generate

COPY src ./src

EXPOSE 8000

CMD ["sh", "-c", "uv run prisma generate && uv run prisma db push --accept-data-loss --skip-generate && uv run prisma db execute --file /app/prisma/backfill_catalog_id.sql && uv run prisma db execute --file /app/prisma/backfill_printing_set_id.sql && uv run prisma db execute --file /app/prisma/bulk_import.sql && uv run prisma db execute --file /app/prisma/remove_legacy_scryfall_images.sql && uv run prisma db execute --file /app/prisma/indexes_optimization.sql && exec uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload"]
