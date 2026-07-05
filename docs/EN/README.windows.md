# TG WS Proxy for Windows

Go to the [releases page](https://github.com/Flowseal/tg-ws-proxy/releases) and download:

- `TgWsProxy_windows.exe` (Windows 10+ x64)
- `TgWsProxy_windows_arm64.exe` (Windows 10+ ARM64)
- `TgWsProxy_windows_7_64bit.exe` (Windows 7 x64)
- `TgWsProxy_windows_7_32bit.exe` (Windows 7 x32)

Builds are published automatically via [GitHub Actions](https://github.com/Flowseal/tg-ws-proxy/actions) from open source code.

On first launch, a window will open with instructions for connecting Telegram Desktop. **The application minimizes to system tray.**

## Tray Menu

- **Open in Telegram** — automatically configure proxy via `tg://proxy` link
- **Copy Link** — copy the proxy connection link
- **Restart Proxy** — restart without exiting the application
- **Settings...** — GUI configuration editor (app version, optional GitHub update checks)
- **Open Logs** — open log file
- **Exit** — stop proxy and close application

On first launch after startup, you may be prompted to open the release page if a new version is available on GitHub (this check can be disabled in settings).

## Configuring Telegram Desktop

### Automatic Setup

Right-click the tray icon and select **"Open in Telegram"**.

If it doesn't work (Telegram doesn't open with proxy), follow these steps:

1. Right-click the tray icon and select **"Copy Link"**
2. Send the link to "Saved Messages" in Telegram and click it
3. Connect

### Manual Setup

1. Telegram → **Settings** → **Advanced** → **Connection type** → **Proxy**
2. Add proxy:
   - **Type:** MTProto
   - **Server:** `127.0.0.1` (or your custom address)
   - **Port:** `1443` (or your custom port)
   - **Secret:** from settings or logs

## Portable Mode

Portable mode is automatically enabled if a folder named `TgWsProxy_data` exists next to the executable.  
You can also force portable mode by running the executable with the `--portable` parameter (it will create the folder).

## Building from Source

Detailed instructions: [BuildFromSource.md](./BuildFromSource.md)

```bash
pip install -e .
tg-ws-proxy-tray-win
```
