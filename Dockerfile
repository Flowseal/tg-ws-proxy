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
RUN "$VIRTUAL_ENV/bin/pip" install cryptography==46.0.5 certifi "httpx[http2]==0.28.1"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    TG_WS_PROXY_SECRET=""  \
    TG_WS_PROXY_CF_WORKER=""

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates iptables \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY proxy ./proxy
COPY utils ./utils
COPY configuration ./configuration
COPY deploy/run_proxy.py ./deploy/run_proxy.py
COPY deploy/linux/dpi.py ./deploy/linux/dpi.py
COPY docs/README.md LICENSE ./

USER app

EXPOSE 1443/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/venv/bin/python", "/app/deploy/run_proxy.py"]
CMD []

# Optional packet processor. Ordinary proxy images retain their unprivileged entrypoint.
FROM python:3.12-slim AS nfqws-builder
COPY deploy/linux/nfqws.py /tmp/nfqws.py
RUN python /tmp/nfqws.py build

FROM python:3.12-slim AS nfqws
RUN apt-get update \
    && apt-get install -y --no-install-recommends iptables libnetfilter-queue1 libnfnetlink0 libcap2 libmnl0 zlib1g \
    && rm -rf /var/lib/apt/lists/*
COPY --from=nfqws-builder /opt/tg-ws-proxy-nfqws /opt/tg-ws-proxy-nfqws
COPY deploy/linux/nfqws.py deploy/linux/dpi.py /app/deploy/linux/
ENTRYPOINT ["python", "/app/deploy/linux/nfqws.py", "container"]

# Keep docker build's default target compatible with ordinary docker run.
FROM runtime AS final
