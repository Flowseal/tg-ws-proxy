# syntax=docker/dockerfile:1.7
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx openssl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY configuration ./configuration
COPY deploy ./deploy
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/docker/entrypoint.py", "cover"]
