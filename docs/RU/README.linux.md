# TG WS Proxy для Linux

## Готовые сборки

Для Debian/Ubuntu скачайте со [страницы релизов](https://github.com/Flowseal/tg-ws-proxy/releases) пакет `TgWsProxy_linux_amd64.deb`.

Для Arch и основанных на Arch дистрибутивов подготовлены пакеты в AUR:

- [tg-ws-proxy-bin](https://aur.archlinux.org/packages/tg-ws-proxy-bin)
- [tg-ws-proxy-git](https://aur.archlinux.org/packages/tg-ws-proxy-git)
- [tg-ws-proxy-cli](https://aur.archlinux.org/packages/tg-ws-proxy-cli)

```shell
# Установка без AUR-helper
git clone https://aur.archlinux.org/tg-ws-proxy-bin.git
cd tg-ws-proxy-bin
makepkg -si

# При помощи AUR-helper
paru -S tg-ws-proxy-bin

# Для пакета -cli запуск через systemd (8888 — номер порта; secret можно сгенерировать командой openssl rand -hex 16)
sudo systemctl start tg-ws-proxy@8888:3075abe65830f0325116bb0416cadf9f
```

Для остальных дистрибутивов можно использовать `TgWsProxy_linux_amd64` (бинарный файл для x86_64).

```bash
chmod +x TgWsProxy_linux_amd64
./TgWsProxy_linux_amd64
```

При первом запуске откроется окно с инструкцией. Приложение работает в системном трее (требуется AppIndicator).

## Настройка Telegram Desktop

1. Telegram → **Настройки** → **Продвинутые настройки** → **Тип подключения** → **Прокси**
2. Добавьте прокси:
   - **Тип:** MTProto
   - **Сервер:** `127.0.0.1` (или переопределенный вами)
   - **Порт:** `1443` (или переопределенный вами)
   - **Secret:** из настроек или логов

## Установка из исходников

Подробная инструкция: [BuildFromSource.md](./BuildFromSource.md)

```bash
pip install -e .
tg-ws-proxy-tray-linux
```
