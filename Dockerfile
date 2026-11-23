FROM node:22-trixie-slim AS node-builder

WORKDIR /build
COPY matter_bridge/package.json matter_bridge/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22-trixie-slim AS css-builder

WORKDIR /css
COPY package.json package-lock.json tailwind.config.js ./
RUN npm ci --no-audit --no-fund
COPY static/ ./static/
COPY src/ui/ ./src/ui/
RUN npm run build:css

FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="Nobis.Supervisor" \
    org.opencontainers.image.description="Supervision haute fidélité et passerelle Matter locale pour poêle Nobis" \
    org.opencontainers.image.version="2.4.0" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/malexxx/Nobis.Supervisor" \
    org.opencontainers.image.vendor="Nobis.Supervisor"

ARG APP_VERSION=2.4.0
ARG DOCKER_VERSION=v2.4.0
ARG GIT_COMMIT=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris \
    APP_VERSION=${APP_VERSION} \
    DOCKER_VERSION=${DOCKER_VERSION} \
    NOBIS_IMAGE_COMMIT=${GIT_COMMIT}

ARG ACME_SH_VERSION=3.1.0
ARG ACME_SH_SHA256=5bc8a72095e16a1a177d1a516728bbd3436abf8060232d5d36b462fce74447aa

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
    && python -c "import hashlib, io, os, tarfile, urllib.request; data = urllib.request.urlopen('https://github.com/acmesh-official/acme.sh/archive/refs/tags/${ACME_SH_VERSION}.tar.gz', timeout=30).read(); got = hashlib.sha256(data).hexdigest(); assert got == '${ACME_SH_SHA256}', f'SHA256 mismatch: {got}'; os.makedirs('/usr/local/share/acme.sh', exist_ok=True); tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:gz'); members = [m for m in tf.getmembers() if '/' in m.name]; [setattr(m, 'name', m.name.split('/', 1)[1]) for m in members]; tf.extractall('/usr/local/share/acme.sh', members=members, filter='data')" \
    && chmod +x /usr/local/share/acme.sh/acme.sh \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . /app/
COPY --from=css-builder /css/static/css/tailwind.generated.css /app/static/css/tailwind.generated.css
COPY --from=node-builder /build/node_modules /app/matter_bridge/node_modules
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN groupadd --system --gid 10001 nobis \
    && useradd --system --uid 10001 --gid nobis --home-dir /app --no-create-home nobis \
    && mkdir -p /app/data \
    && chown -R root:root /app \
    && chown -R nobis:nobis /app/data \
    && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
VOLUME ["/app/data"]

EXPOSE 8080 5540/udp 5540/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8080/health', timeout=3, context=ssl._create_unverified_context())" 2>/dev/null || python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,::1", "--timeout-graceful-shutdown", "5"]
