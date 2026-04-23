# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cargo libffi-dev libssl-dev \
    && python -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/pip" install --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN "$VIRTUAL_ENV/bin/pip" install cryptography==46.0.5

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    TG_WS_PROXY_HOST=0.0.0.0 \
    TG_WS_PROXY_PORT=1443 \
    TG_WS_PROXY_SECRET=""  \
    TG_WS_PROXY_DC_IPS="2:149.154.167.220 4:149.154.167.220"

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY proxy ./proxy
COPY docs/README.md LICENSE ./

USER app

EXPOSE 1443/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "-lc", "set -eu; args=\"--host ${TG_WS_PROXY_HOST} --port ${TG_WS_PROXY_PORT}\"; for dc in ${TG_WS_PROXY_DC_IPS}; do args=\"$args --dc-ip $dc\"; done; if [ -n \"${TG_WS_PROXY_SECRET}\" ]; then args=\"$args --secret ${TG_WS_PROXY_SECRET}\"; fi; exec /opt/venv/bin/python -u proxy/tg_ws_proxy.py $args \"$@\"", "--"]
CMD []
