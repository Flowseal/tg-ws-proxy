# TG WS Proxy for macOS

Go to the [releases page](https://github.com/Flowseal/tg-ws-proxy/releases) and download `TgWsProxy_macos_universal.dmg` (universal build for Apple Silicon and Intel).

1. Open the image
2. Drag `TG WS Proxy.app` to the `Applications` folder
3. On first launch, macOS may ask for confirmation: **System Settings → Privacy & Security → Open Anyway**

Before enabling login startup or installing updates, make sure the application has been moved from the DMG to the `Applications` folder.

Minimum supported versions:

- Intel macOS 10.15+
- Apple Silicon macOS 11.0+

## Menu Bar

- **Open in Telegram** — automatically configure the proxy through a `tg://proxy` link
- **Copy Link** — copy the proxy connection link
- **Restart Proxy** — restart without exiting the application
- **Settings...** — GUI editor for configuration, login startup, and update checks
- **Open Logs** — open the log file
- **Update** — download and install a new version (shown when an update is available)
- **Exit** — stop the proxy and close the application

Login startup can be enabled in settings. If the application is moved after enabling startup, disable and enable this setting again.

When a new version is available, the application can install the official DMG and restart. The checksum and application signature are verified before installation, and the previous version is kept for recovery. When running from source, the release page is opened instead.

## Configuring Telegram Desktop

1. Telegram → **Settings** → **Advanced** → **Connection type** → **Proxy**
2. Add proxy:
   - **Type:** MTProto
   - **Server:** `127.0.0.1` (or your custom address)
   - **Port:** `1443` (or your custom port)
   - **Secret:** from settings or logs

## Building from Source

Detailed instructions: [BuildFromSource.md](./BuildFromSource.md)

The interface requires Tk, CustomTkinter, and access to Cocoa via PyObjC. They are installed automatically, except for Tk, which must be included in your Python build.

```bash
pip install -e .
tg-ws-proxy-tray-macos
```
