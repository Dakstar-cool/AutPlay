FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS node
FROM mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585 AS media
FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3 AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git chromium chromium-sandbox \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=media /ffmpeg /usr/local/bin/ffmpeg
COPY --from=media /ffprobe /usr/local/bin/ffprobe
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /opt/acquisition
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY docker/entrypoint.sh /usr/local/bin/acquisition-entrypoint
RUN chmod 755 /usr/local/bin/acquisition-entrypoint \
    && groupadd --gid 1000 acquisition \
    && useradd --uid 1000 --gid 1000 --no-create-home acquisition

FROM dependencies AS checks
RUN uv sync --frozen \
    && uv run --frozen python -m playwright install --with-deps firefox
COPY tests ./tests
COPY docker ./docker
RUN uv run --frozen python -m pytest -q \
    && uv run --frozen python -m mypy --config-file pyproject.toml src

FROM dependencies AS runtime
USER acquisition:acquisition
ENTRYPOINT ["/usr/local/bin/acquisition-entrypoint"]
