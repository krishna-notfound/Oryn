# syntax=docker/dockerfile:1
# Multi-stage: the builder holds pip/build tooling, the runtime image only the venv + app.

FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
# Copy only what the package build needs so dependency layers stay cached.
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    MEMORY_DB_PATH=/data/memory.db

RUN useradd --system --uid 10001 --home-dir /srv --shell /usr/sbin/nologin oryn \
    && mkdir /data && chown oryn:oryn /data

COPY --from=builder /opt/venv /opt/venv
# app/ is also on the working directory path so non-.py package files are never missed.
WORKDIR /srv
COPY --chown=oryn:oryn app ./app

USER oryn
VOLUME /data
EXPOSE 8000

# stdlib only: no curl in the slim image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# One worker on purpose: WebSocket sessions live in-process. Port 8000 is never published, only
# Caddy can reach it, so trusting X-Forwarded-* from any peer (the Caddy container IP) is safe.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "10"]
