FROM node:22-trixie-slim AS node-builder

WORKDIR /build
COPY matter_bridge/package.json matter_bridge/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.11-slim

ARG APP_VERSION="2.0.1"
ARG DOCKER_VERSION="v2.0.1"

LABEL org.opencontainers.image.title="Nobis.Supervisor" \
    org.opencontainers.image.description="Supervision haute fidélité et passerelle Matter locale pour poêle Nobis" \
    org.opencontainers.image.version="2.0.1"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris \
    APP_VERSION="${APP_VERSION}" \
    DOCKER_VERSION="${DOCKER_VERSION}"

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade "pip" "setuptools>=84.1.0" "wheel" \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -f /usr/local/lib/python*/site-packages/pip/_vendor/bom.cdx.json

COPY . /app/
COPY --from=node-builder /build/node_modules /app/matter_bridge/node_modules
RUN mkdir -p /app/data

EXPOSE 8080 5540/udp 5540/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
