# TG WS Proxy for Docker

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

All settings are configured using environment variables when running the container:

| Variable                | Description                      | Default                           |
| ----------------------- | -------------------------------- | --------------------------------- |
| `TG_WS_PROXY_HOST`      | Address for incoming connections | `0.0.0.0`                        |
| `TG_WS_PROXY_PORT`      | Port inside the container        | `1443`                           |
| `TG_WS_PROXY_SECRET`    | Secret key                       | `random`                         |
| `TG_WS_PROXY_DC_IPS`    | DC number:IP pairs separated by space | `2:149.154.167.220 4:149.154.167.220` |
| `TG_WS_PROXY_CF_WORKER` | Cloudflare Worker domain         | `None`                           |

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

## Running via Makefile

For local use there is a `Makefile`. Settings live in `.env` (template —
`.env.example`), and the proxy secret lives in `.secret`.

```bash
# create .env from the template (edit it if needed)
make env

# build the image
make build

# run the container and print the tg://proxy link
make run
```

Common commands:

| Command          | Action                              |
| ---------------- | ----------------------------------- |
| `make build`     | build the image                     |
| `make run`       | run the container                   |
| `make stop`      | stop the container                  |
| `make restart`   | restart the container               |
| `make logs`      | show logs                           |
| `make link`      | print the `tg://proxy` link         |
| `make secret`    | print the current secret            |
| `make install`   | install the systemd service (autostart) |
| `make uninstall` | remove the systemd service          |

### Persistent secret

If `TG_WS_PROXY_SECRET` is empty in `.env`, the first `make run` creates the
secret in the `.secret` file and reuses it afterwards — including across
reboots. This lets you configure Telegram once and keep the same link. Do not
delete `.secret`.

### Autostart (systemd)

```bash
make install      # installs and enables the tg-ws-proxy service
sudo systemctl start tg-ws-proxy
```

## Configuring Telegram Desktop

1. Telegram → **Settings** → **Advanced** → **Connection type** → **Proxy**
2. Add proxy:
   - **Type:** MTProto
   - **Server:** `127.0.0.1` (or your custom address)
   - **Port:** `1443` (or your custom port)
   - **Secret:** from settings or logs
