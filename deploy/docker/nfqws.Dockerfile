# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
COPY deploy/linux/nfqws.py /tmp/nfqws.py
RUN python /tmp/nfqws.py build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends iptables libnetfilter-queue1 \
       libnfnetlink0 libcap2 libmnl0 zlib1g \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /opt/tg-ws-proxy-nfqws /opt/tg-ws-proxy-nfqws
COPY configuration ./configuration
COPY deploy/linux ./deploy/linux
COPY deploy/docker/entrypoint.py ./deploy/docker/entrypoint.py
ENTRYPOINT ["python", "/app/deploy/docker/entrypoint.py", "nfqws"]
