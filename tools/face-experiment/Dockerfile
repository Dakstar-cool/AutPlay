FROM ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_NO_DEV=1 PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=2 TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 CUDA_VISIBLE_DEVICES=-1
WORKDIR /opt/face
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
RUN apt-get update \
    && apt-get install --no-install-recommends -y libatomic1=14.2.0-19 \
    && rm -rf /var/lib/apt/lists/*
COPY reference.py evaluate_audio.py audio_inputs.py artifacts.lock.json ./
USER 65532:65532
ENTRYPOINT ["/opt/face/.venv/bin/python", "/opt/face/reference.py"]
