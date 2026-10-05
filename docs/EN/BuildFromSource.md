# Building from Source

## Console Proxy

To run only the proxy without the system tray interface, basic installation is sufficient:

```bash
pip install -e .
tg-ws-proxy
```

## Tray Application by OS

### Windows 7/10+

```bash
pip install -e .
tg-ws-proxy-tray-win
```

### macOS

Requires a Python build with Tk support. You can verify it with the command `python3 -m tkinter`.

```bash
pip install -e .
tg-ws-proxy-tray-macos
```

### Linux

```bash
pip install -e .
tg-ws-proxy-tray-linux
```

## Console Mode from Source

```bash
tg-ws-proxy [--port PORT] [--host HOST] [--dc-ip DC:IP ...] [-v]
```

**Arguments:**

| Argument | Default | Description |
|---|---|---|
| `--port` | `1443` | Proxy port |
| `--host` | `127.0.0.1` | Proxy host |
| `--secret` | `random` | 32-character hex key for client authorization |
| `--dc-ip` | `2:149.154.167.220`, `4:149.154.167.220` | Target IP for DC (can be specified multiple times) |
| `--no-cfproxy` | `false` | Disable [Cloudflare proxying](./CfProxy.md) attempts |
| `--no-h2` | `false` | Disable HTTP/2 media multiplexing through CF proxy |
| `--cfproxy-domain` | | Specify your own domain for Cloudflare proxying [Learn more](./CfProxy.md). Can be specified multiple times. |
| `--cfproxy-worker-domain` | | Cloudflare Worker domain [Learn more](./CfWorker.md). Can be specified multiple times. |
| `--no-secure` | `false` | Use 80 port for CF-proxy and CF-worker connections |
| `--fake-tls-domain` | | Enable Fake TLS masquerading (ee-secret) with specified SNI domain |
| `--proxy-protocol` | disabled | Accept HAProxy PROXY protocol v1 (for use behind nginx/haproxy with `proxy_protocol on`) |
| `--force-test-dc` | `false` | Route all traffic to Telegram test DCs [Learn more](./TestDc.md) |
| `--buf-kb` | `0` | Buffer size in KB (0 = OS auto-tuning) |
| `--pool-size` | `4` | Number of ready WS connections per DC. `0` disables the direct DC→IP WS route |
| `--log-file` | disabled | Path to file for saving logs |
| `--log-max-mb` | `5` | Maximum log file size in MB (afterwards overwrites) |
| `--log-backups` | `1` | Number of rotated log files to keep (minimum 1) |
| `-v`, `--verbose` | disabled | Verbose logging (DEBUG) |

**Examples:**

```bash
# Standard startup
tg-ws-proxy --secret 00112233445566778899aabbccddeeff

# Different port and additional DCs
tg-ws-proxy --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# With verbose logging, without direct connections to DCs
tg-ws-proxy -v --dc-ip

# Fake TLS masquerading (ee-secret)
tg-ws-proxy --fake-tls-domain example.com
```
