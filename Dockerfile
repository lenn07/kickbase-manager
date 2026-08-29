# syntax=docker/dockerfile:1.7

# ---- Build-Stage ----
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --prefix=/install .

# ---- Runtime-Stage ----
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin \
    KB_DATA_DIR=/data \
    KB_HOST=0.0.0.0 \
    KB_PORT=8000

RUN groupadd --system --gid 1000 kb \
    && useradd  --system --uid 1000 --gid kb --home-dir /app --no-create-home kb \
    && mkdir -p /data \
    && chown kb:kb /data

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=kb:kb app ./app

USER kb
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
