FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /opt/autplay

COPY server/pyproject.toml server/uv.lock server/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY server/src ./src
RUN uv sync --frozen --no-dev --no-editable

COPY server/alembic.ini ./alembic.ini
COPY server/migrations ./migrations

FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3

RUN groupadd --system autplay \
    && useradd --system --gid autplay --home-dir /nonexistent --shell /usr/sbin/nologin autplay

COPY --from=builder --chown=autplay:autplay /opt/autplay /opt/autplay

ENV PATH="/opt/autplay/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
WORKDIR /opt/autplay
USER autplay:autplay

EXPOSE 8787
CMD ["autplay-api"]
