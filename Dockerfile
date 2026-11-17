FROM node:22-trixie-slim AS node-builder

WORKDIR /build
COPY matter_bridge/package.json matter_bridge/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="Nobis.Supervisor" \
    org.opencontainers.image.description="Supervision haute fidélité et passerelle Matter locale pour poêle Nobis" \
    org.opencontainers.image.version="2.1.0" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/malexxx/Nobis.Supervisor" \
    org.opencontainers.image.vendor="Nobis.Supervisor"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    tzdata \
    tini \
    gosu \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . /app/
COPY --from=node-builder /build/node_modules /app/matter_bridge/node_modules
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN groupadd --system --gid 10001 nobis \
    && useradd --system --uid 10001 --gid nobis --home-dir /app --no-create-home nobis \
    && mkdir -p /app/data \
    && chown -R nobis:nobis /app \
    && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
VOLUME ["/app/data"]

EXPOSE 8080 5540/udp 5540/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
