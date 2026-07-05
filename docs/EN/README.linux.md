# TG WS Proxy for Linux

## Prebuilt Packages

For Debian/Ubuntu, download the `TgWsProxy_linux_amd64.deb` package from the [releases page](https://github.com/Flowseal/tg-ws-proxy/releases).

For Arch and Arch-based distributions, packages are available in AUR:

- [tg-ws-proxy-bin](https://aur.archlinux.org/packages/tg-ws-proxy-bin)
- [tg-ws-proxy-git](https://aur.archlinux.org/packages/tg-ws-proxy-git)
- [tg-ws-proxy-cli](https://aur.archlinux.org/packages/tg-ws-proxy-cli)

```shell
# Installation without AUR helper
git clone https://aur.archlinux.org/tg-ws-proxy-bin.git
cd tg-ws-proxy-bin
makepkg -si

# Using AUR helper
paru -S tg-ws-proxy-bin

# For -cli package, run via systemd (8888 — port number; secret can be generated with openssl rand -hex 16)
sudo systemctl start tg-ws-proxy@8888:3075abe65830f0325116bb0416cadf9f
```

For other distributions, you can use `TgWsProxy_linux_amd64` (binary for x86_64).

```bash
chmod +x TgWsProxy_linux_amd64
./TgWsProxy_linux_amd64
```

On first launch, a window will open with instructions. The application runs in the system tray (AppIndicator required).

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
tg-ws-proxy-tray-linux
```
