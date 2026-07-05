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

## Configuring Telegram Desktop

1. Telegram → **Settings** → **Advanced** → **Connection type** → **Proxy**
2. Add proxy:
   - **Type:** MTProto
   - **Server:** `127.0.0.1` (or your custom address)
   - **Port:** `1443` (or your custom port)
   - **Secret:** from settings or logs
