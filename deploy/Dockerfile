# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Cached dependency layer: install everything except the project itself,
# so dep changes (not src/ edits) bust this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --frozen --no-install-project --extra api

COPY pyproject.toml README.md LICENSE uv.lock ./
COPY src ./src

# Project layer: install fastssv itself non-editably for a slim image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --extra api


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000 \
    FASTSSV_API_LOG_LEVEL=INFO

RUN groupadd --system --gid 1000 fastssv \
    && useradd --system --uid 1000 --gid fastssv --shell /usr/sbin/nologin fastssv

COPY --from=builder --chown=fastssv:fastssv /app/.venv /app/.venv

WORKDIR /app
USER fastssv

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{__import__(\"os\").environ.get(\"PORT\",\"8000\")}/v1/health', timeout=3).status == 200 else 1)"

# --forwarded-allow-ips controls which peers gunicorn (and through it,
# uvicorn's ProxyHeadersMiddleware) trusts to set X-Forwarded-For and
# X-Forwarded-Proto. Defaults to 127.0.0.1 (a same-host reverse proxy
# such as nginx/caddy in another container on the same docker network
# typically presents as 127.0.0.1 only when host networking is used —
# bridged setups need the proxy's actual subnet, e.g. "172.16.0.0/12").
# Set to "*" only if you fully control the inbound network path; trusting
# a header from an untrusted source lets clients spoof their identity.
CMD ["sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile - --timeout 30 --graceful-timeout 30 --forwarded-allow-ips ${FORWARDED_ALLOW_IPS:-127.0.0.1} fastssv.api.app:app"]
