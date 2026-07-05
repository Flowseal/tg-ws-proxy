# TG WS Proxy for macOS

Go to the [releases page](https://github.com/Flowseal/tg-ws-proxy/releases) and download `TgWsProxy_macos_universal.dmg` (universal build for Apple Silicon and Intel).

1. Open the image
2. Drag `TG WS Proxy.app` to the `Applications` folder
3. On first launch, macOS may ask for confirmation: **System Settings → Privacy & Security → Open Anyway**

Minimum supported versions:

- Intel macOS 10.15+
- Apple Silicon macOS 11.0+

## Configuring Telegram Desktop

1. Telegram → **Settings** → **Advanced** → **Connection type** → **Proxy**
2. Add proxy:
   - **Type:** MTProto
   - **Server:** `127.0.0.1` (or your custom address)
   - **Port:** `1443` (or your custom port)
   - **Secret:** from settings or logs

## Building from Source

Detailed instructions: [BuildFromSource.md](./BuildFromSource.md)

```bash
pip install -e .
tg-ws-proxy-tray-macos
```
