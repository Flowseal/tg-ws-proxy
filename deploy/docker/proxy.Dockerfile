# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install cryptography==46.0.5 certifi "httpx[http2]==0.28.1"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /tmp app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY proxy ./proxy
COPY utils ./utils
COPY configuration ./configuration
COPY deploy/docker/entrypoint.py ./deploy/docker/entrypoint.py
USER 10001:10001
EXPOSE 1443/tcp
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/app/deploy/docker/entrypoint.py", "proxy"]
