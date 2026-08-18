FROM mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585 AS ffmpeg-tools

FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_HTTP_RETRIES=10 \
    UV_HTTP_CONNECT_TIMEOUT=30 \
    UV_HTTP_TIMEOUT=300
WORKDIR /opt/autplay

COPY server/pyproject.toml server/uv.lock server/README.md ./server/
COPY server/src ./server/src
COPY gpu/pyproject.toml gpu/uv.lock gpu/README.md ./gpu/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project gpu --frozen --no-dev --no-install-project

COPY gpu/src ./gpu/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project gpu --frozen --no-dev --no-editable
RUN /opt/autplay/gpu/.venv/bin/python -c \
    "import onnxruntime as ort; assert 'CUDAExecutionProvider' in ort.get_available_providers(), ort.get_available_providers()"

FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3

RUN groupadd --system autplay \
    && useradd --system --gid autplay --home-dir /nonexistent --shell /usr/sbin/nologin autplay \
    && install -d -o autplay -g autplay -m 0700 /var/lib/autplay/models

COPY --from=builder --chown=autplay:autplay /opt/autplay /opt/autplay
COPY --from=ffmpeg-tools --chown=root:root /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-tools --chown=root:root /ffprobe /usr/local/bin/ffprobe

RUN test "$(sha256sum /usr/local/bin/ffmpeg | cut -d ' ' -f 1)" = \
        "7b3fb9508c20166ab3ba236a9585c3e22e903880723c1a6448e69ae6e4cd88d2" \
    && test "$(sha256sum /usr/local/bin/ffprobe | cut -d ' ' -f 1)" = \
        "fe39eb91eb04dd18dff3870a87b59e41be997476c2d373c46ff7e12bb284743c"

ENV PATH="/opt/autplay/gpu/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
WORKDIR /opt/autplay/gpu
USER autplay:autplay

CMD ["autplay-ml-gpu"]
