FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3 AS media-tools

ADD --checksum=sha256:fc16cd37a70168040bc9ceb45f1d4d1216f5a75bc4c9cf8564bea70ac6a45733 \
    https://github.com/acoustid/chromaprint/releases/download/v1.6.1/chromaprint-fpcalc-1.6.1-linux-x86_64.tar.gz \
    /tmp/chromaprint-fpcalc.tar.gz
RUN mkdir -p /opt/chromaprint \
    && tar -xzf /tmp/chromaprint-fpcalc.tar.gz \
        --strip-components=1 \
        -C /opt/chromaprint \
        chromaprint-fpcalc-1.6.1-linux-x86_64/fpcalc \
    && test "$(sha256sum /opt/chromaprint/fpcalc | cut -d ' ' -f 1)" = \
        "e7b14fbf9d544f6ba99b7aced3c07786258e09e37cfcb054a41d2a6eeb0887a7"

FROM mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585 AS ffmpeg-tools

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
    && useradd --system --gid autplay --home-dir /nonexistent --shell /usr/sbin/nologin autplay \
    && install -d -o autplay -g autplay -m 0700 /var/lib/autplay/vault

COPY --from=builder --chown=autplay:autplay /opt/autplay /opt/autplay
COPY --from=media-tools --chown=root:root /opt/chromaprint/fpcalc /usr/local/bin/fpcalc
COPY --from=ffmpeg-tools --chown=root:root /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-tools --chown=root:root /ffprobe /usr/local/bin/ffprobe

RUN test "$(sha256sum /usr/local/bin/ffmpeg | cut -d ' ' -f 1)" = \
        "7b3fb9508c20166ab3ba236a9585c3e22e903880723c1a6448e69ae6e4cd88d2" \
    && test "$(sha256sum /usr/local/bin/ffprobe | cut -d ' ' -f 1)" = \
        "fe39eb91eb04dd18dff3870a87b59e41be997476c2d373c46ff7e12bb284743c" \
    && ffprobe -version | grep -F "ffprobe version 8.1.2" \
    && ffmpeg -version | grep -F "ffmpeg version 8.1.2" \
    && fpcalc -version | grep -F "fpcalc version 1.6.1"

ENV PATH="/opt/autplay/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
WORKDIR /opt/autplay
USER autplay:autplay

EXPOSE 8787 8788
CMD ["autplay-api"]
