FROM node:22-trixie-slim AS node-builder

WORKDIR /build
COPY matter_bridge/package.json matter_bridge/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="Nobis.Supervisor" \
    org.opencontainers.image.description="Supervision haute fidélité et passerelle Matter locale pour poêle Nobis" \
    org.opencontainers.image.version="2.1.2" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/malexxx/Nobis.Supervisor" \
    org.opencontainers.image.vendor="Nobis.Supervisor"

ARG APP_VERSION=2.1.2
ARG DOCKER_VERSION=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris \
    APP_VERSION=${APP_VERSION} \
    DOCKER_VERSION=${DOCKER_VERSION}

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    tzdata \
    tini \
    gosu \
    openssl \
    wget \
    ca-certificates \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && python -c "import io, os, tarfile, urllib.request; data = urllib.request.urlopen('https://github.com/acmesh-official/acme.sh/archive/refs/tags/3.1.0.tar.gz', timeout=30).read(); os.makedirs('/usr/local/share/acme.sh', exist_ok=True); tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:gz'); members = [m for m in tf.getmembers() if '/' in m.name]; [setattr(m, 'name', m.name.split('/', 1)[1]) for m in members]; tf.extractall('/usr/local/share/acme.sh', members=members)" \
    && chmod +x /usr/local/share/acme.sh/acme.sh \
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
    CMD python -c "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8080/health', timeout=3, context=ssl._create_unverified_context())" 2>/dev/null || python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "5"]
