# TG WS Proxy for Docker

## Prebuilt Docker Hub images and standalone Compose

For a VPS use [the three prebuilt images and static Compose](./DockerHub.md).
Copy `docker-compose.yaml`, `config.toml` and `.env` to the server; no host Python or
server-side build is needed. The three Dockerfiles live in `deploy/docker/`.

```bash
docker compose pull
docker compose up -d --wait --wait-timeout 120
docker compose exec -T proxy python /app/deploy/docker/entrypoint.py link
```

Set the image namespace/tag in `.env` and the VPS address/SNI in TOML first.
The older Python installer and source-build workflow remain documented below.

## Automatic VPS deployment with HTTPS masking

With Docker Engine, Compose v2, Python 3.8+ and OpenSSL installed, run from
the cloned repository root:

```bash
python3 deploy/setup_masking.py --mode docker --domain proxy.example.com
```

Prefer your own domain pointing at the VPS. Public port 443 must be free.
The command creates a cover site, self-signed certificate, persistent secret
and Compose config, builds and starts nginx + proxy, checks HTTPS through
the proxy, and prints an `ee` link. Use an existing trusted certificate with
`--cert /path/fullchain.pem --key /path/privkey.pem`. Self-signed certificates
remain recognizable as self-signed.

State is stored in `.tgws-masking/`. Repeat the command with the same options
to preserve the secret, certificate and website. Override the directory with
`--state-dir` or public port with `--port 2443`. Add `--prepare-only` to generate
files without starting containers.

```bash
docker compose -f .tgws-masking/compose.yaml ps
docker compose -f .tgws-masking/compose.yaml logs proxy
docker compose -f .tgws-masking/compose.yaml down
```

Only the proxy port is published; nginx is on a private network and only nginx
receives the TLS-key mount. `down` preserves state files. The installer waits
for healthchecks that exercise real HTTPS through masking. TCPMSS=88 is
configured inside the proxy container on every start, before dropping to `app`.
The generated service briefly uses root + NET_ADMIN with no-new-privileges;
it does not modify host firewall rules. Use `--tcpmss 92` to tune MSS or
`--no-tcpmss` to recreate the service without the bootstrap and capability.
Rootless Docker or a kernel without TCPMSS may require the opt-out. Docker
Desktop/userland forwarding may terminate the external TCP connection outside
this namespace; check the client-facing MSS on a native Linux VPS.
nfqws is built automatically from zapret v72.12 in a separate image and runs
with NET_ADMIN/NET_RAW. The proxy shares its network namespace; the nfqws
container publishes the port. NFQUEUE rules apply before Docker NAT and are
restored at startup. Its healthcheck verifies that queue 200 has a handler.
Use `--nfqws-ttl 8` to tune TTL or `--no-nfqws` to remove the processor on rerun.
For rootless Docker use `--no-tcpmss --no-nfqws`. Plain `docker run` retains
its previous behavior; automatic nfqws is provided by the Compose installer. See [Client ↔ VPS protection](./ClientDpi.md).

Anti-replay runs automatically in memory: up to 8192 entries with a
241-second retention window. Process restarts clear the cache; no database
or separate volume is needed.

## Installation from Source

Enter the commands sequentially, one by one:

```bash
# Clone the repository
git clone https://github.com/Flowseal/tg-ws-proxy.git

# Navigate to the project folder
cd tg-ws-proxy

# Build the image
docker build -t tg-ws-proxy .

# Run the container
docker run -d \
  --name tg-ws-proxy \
  --restart=always \
  -p 1443:1443 \
  tg-ws-proxy:latest

# Get the connection link
docker logs tg-ws-proxy 2>&1 | grep 'tg://proxy'
```

After running the last command, you will see a link like:

```text
tg://proxy?server=172.17.0.2&port=1443&secret=dd68f127db1d...
```

## Configuring Parameters

Configure the container using environment variables. Additional CLI flags can
also be passed after the image name:

| Variable                | Description                                        | Default                               |
| ----------------------- | -------------------------------------------------- | ------------------------------------- |
| `TG_WS_PROXY_HOST`      | Address for incoming connections                   | `0.0.0.0`                             |
| `TG_WS_PROXY_PORT`      | Port inside the container                          | `1443`                                |
| `TG_WS_PROXY_SECRET`    | Secret key                                         | `random`                              |
| `TG_WS_PROXY_DC_IPS`    | DC number:IP pairs separated by space              | `2:149.154.167.220 4:149.154.167.220` |
| `TG_WS_PROXY_CF_WORKER` | Cloudflare Worker domain                           | `None`                                |
| `TG_WS_PROXY_NO_SECURE` | Use 80 port for CF-proxy and CF-worker connections | `False`                               |
| `TG_WS_PROXY_FAKE_TLS_DOMAIN` | Enable Fake TLS with this SNI | empty |
| `TG_WS_PROXY_FAKE_TLS_MASK_HOST` | HTTPS backend; automatic Compose uses `cover` | SNI domain |
| `TG_WS_PROXY_FAKE_TLS_MASK_PORT` | HTTPS backend port | `443` |
| `TG_WS_PROXY_FAKE_TLS_DRS` | DRS when Fake TLS is enabled | `true` |
| `TG_WS_PROXY_FAKE_TLS_SPLIT` | Split ServerHello writes | `true` |
| `TG_WS_PROXY_FAKE_TLS_SPLIT_DELAY_MS` | Base split delay | `5` |
| `TG_WS_PROXY_FAKE_TLS_SPLIT_JITTER_MS` | Maximum extra split delay | `10` |
| `TG_WS_PROXY_CF_DOMAIN` | CF proxy domain | automatic |
| `TG_WS_PROXY_POOL_SIZE` | WS pool size per DC | `4` |
| `TG_WS_PROXY_NO_H2` | Disable CF media HTTP/2 | `false` |
| `TG_WS_PROXY_NO_CFPROXY` | Disable CF proxy | `false` |
| `TG_WS_PROXY_TCPMSS` | Configure TCPMSS before dropping to `app` | `false`; automatic installer sets `true` |
| `TG_WS_PROXY_TCPMSS_MSS` | Advertised MSS when bootstrap is enabled | `88` |

Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`, case-insensitively.
Setting the Fake TLS domain alone in a plain `docker run` does not provision
an HTTPS backend: use the installer or provide one. Inside a container,
`127.0.0.1` refers to that container; use the nginx service name on a shared
network for a separate backend container.

For manual `docker run` with TCPMSS, use `--user 0:0 --cap-add NET_ADMIN
--security-opt no-new-privileges=true -e TG_WS_PROXY_TCPMSS=true`. The default
image still runs as `app` without bootstrap. Missing firewall permissions fail
startup rather than silently starting an unprotected listener. IPv4 rules are
verified; unavailable IPv6 is reported explicitly.

Example with manually specified secret:

```bash
docker run -d \
  --name tg-ws-proxy \
  --restart=always \
  -p 1443:1443 \
  -e TG_WS_PROXY_SECRET="your_secret" \
  tg-ws-proxy:latest
```

To generate a secret, you can use:

```bash
openssl rand -hex 16  
```

## Configuring Telegram Desktop

1. Telegram → **Settings** → **Advanced** → **Connection type** → **Proxy**
2. Add proxy:
   - **Type:** MTProto
   - **Server:** public VPS IP/domain; `127.0.0.1` only for local Docker
   - **Port:** published Docker port (`1443` in the manual example, `443` with the installer)
   - **Secret:** from settings or logs
