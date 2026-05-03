# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Use `uv pip install` (not `uv sync`) so only the [api] extras are
# resolved — the [viz] extras have a requires-python conflict that
# would break a full workspace resolve.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --prefix=/install ".[api]"


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    FASTSSV_API_LOG_LEVEL=INFO

RUN groupadd --system --gid 1000 fastssv \
    && useradd --system --uid 1000 --gid fastssv --shell /usr/sbin/nologin fastssv

COPY --from=builder /install /usr/local

WORKDIR /app
USER fastssv

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{__import__(\"os\").environ.get(\"PORT\",\"8000\")}/v1/health', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile - --timeout 30 --graceful-timeout 30 fastssv.api.app:app"]
