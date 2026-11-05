FROM python:3.11-slim

LABEL org.opencontainers.image.title="Nobis.Supervisor" \
      org.opencontainers.image.description="Supervision haute fidélité et passerelle Matter locale pour poêle Nobis" \
      org.opencontainers.image.version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris

RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    nodejs \
    npm \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/
RUN cd /app/matter_bridge && npm install --omit=dev --no-audit --no-fund
RUN mkdir -p /app/data

EXPOSE 8080 5540/udp 5540/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

