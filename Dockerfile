# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12.12
ARG UV_VERSION=0.11.29

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /workspace

FROM base AS quality
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY protocol ./protocol
COPY generated ./generated
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
CMD ["sh", "-c", "uv lock --check && uv run --locked ruff format --check src tests migrations && uv run --locked ruff check src tests migrations && uv run --locked mypy src tests && uv run --locked pytest"]

FROM base AS builder
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
    LABEL org.opencontainers.image.title="Carbon DC Skid 2 Backend" \
      org.opencontainers.image.description="Reusable non-root Python runtime for Skid 2 services" \
      org.opencontainers.image.source="local Skid 2 repository"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 skid2 \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin skid2
WORKDIR /app
COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 protocol ./protocol
COPY --chown=10001:10001 generated/protocol/simulator ./generated/protocol/simulator
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 --chmod=755 entrypoint.sh /usr/local/bin/skid2-entrypoint
USER 10001:10001
EXPOSE 8000 1502
ENTRYPOINT ["/usr/local/bin/skid2-entrypoint"]
CMD ["python", "-m", "skid2.cli", "check-settings"]
