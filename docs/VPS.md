# TG WS Proxy на VPS без GUI

Да, этот проект можно запускать на обычном Linux VPS без графического окружения. GUI-файлы `windows.py`, `macos.py`, `linux.py`, `ui/` и tray-логика нужны только для локального приложения в трее. Сам прокси запускается отдельно через `proxy/tg_ws_proxy.py` или консольную команду `tg-ws-proxy`.

## Что будет работать

На сервере прокси слушает TCP-порт, например `0.0.0.0:1443`, принимает MTProto-подключения от Telegram-клиента и дальше ходит к Telegram DC через WebSocket/TLS или fallback-механизмы проекта.

Схема:

```text
Telegram client -> VPS:1443 -> tg-ws-proxy -> Telegram WebSocket/DC
```

На VPS не нужны X11, Wayland, desktop environment, tray, tkinter, pystray или AppIndicator, если запускать headless-вход.

## Важные условия

- VPS должен иметь исходящий доступ к `*.web.telegram.org:443` и/или fallback-направлениям.
- Входящий TCP-порт, например `1443`, должен быть открыт в firewall VPS и панели провайдера.
- В Telegram нужно указывать публичный IP или домен VPS, а не `127.0.0.1`.
- `secret` должен быть фиксированным. Если не задать его явно, при каждом новом запуске будет генерироваться новый ключ, и старая ссылка перестанет работать.
- Если VPS находится в сети, где сам Telegram/WebSocket недоступен или активно режется, может понадобиться Cloudflare fallback, Worker или Fake TLS/Nginx из существующих инструкций проекта.

## Вариант 1: Docker Compose

На сервере:

```bash
git clone https://github.com/Flowseal/tg-ws-proxy.git
cd tg-ws-proxy/deploy
cp .env.example .env
openssl rand -hex 16
```

Вставьте результат `openssl rand -hex 16` в `TG_WS_PROXY_SECRET` внутри `.env`.

Запуск:

```bash
docker compose up -d --build
docker logs tg-ws-proxy
```

В логах будет строка вида:

```text
tg://proxy?server=0.0.0.0&port=1443&secret=dd...
```

Для подключения замените `server=0.0.0.0` на публичный IP или домен VPS:

```text
tg://proxy?server=YOUR_VPS_IP&port=1443&secret=ddYOUR_SECRET
```

Проверка:

```bash
docker ps
docker logs --tail=80 tg-ws-proxy
ss -lntp | grep 1443
```

## Вариант 2: systemd без Docker

### Быстрая установка

Если приватный deploy-репозиторий уже находится на VPS, можно запустить установщик:

```bash
sudo sh deploy/install-systemd.sh
```

Чтобы сразу получить корректную ссылку с публичным IP, передайте адрес VPS:

```bash
sudo TG_WS_PROXY_PUBLIC_HOST=YOUR_VPS_IP_OR_DOMAIN sh deploy/install-systemd.sh
```

Скрипт:

- создаст системного пользователя `tgwsproxy`;
- скопирует приватную deploy-обертку в `/opt/tg-ws-vps`, если запущен не оттуда;
- скачает чистый core из `https://github.com/Flowseal/tg-ws-proxy.git` в `/opt/tg-ws-proxy-core`;
- создаст `.venv` в `/opt/tg-ws-proxy-core`;
- установит минимальную зависимость `cryptography`;
- создаст `/etc/tg-ws-proxy.env` с постоянным secret;
- включит и запустит `tg-ws-proxy.service`;
- включит ежедневный автоапдейт через `tg-ws-proxy-update.timer`;
- выведет готовую ссылку `tg://proxy?...`.

После запуска посмотрите логи:

```bash
journalctl -u tg-ws-proxy -n 80 --no-pager
```

Проверить автоапдейт:

```bash
systemctl list-timers tg-ws-proxy-update.timer
systemctl status tg-ws-proxy-update.timer
```

Запустить обновление вручную:

```bash
sudo systemctl start tg-ws-proxy-update.service
journalctl -u tg-ws-proxy-update -n 80 --no-pager
```

## Как устроены автообновления

На VPS используются две директории:

```text
/opt/tg-ws-proxy-core  -> чистый публичный core Flowseal/tg-ws-proxy
/opt/tg-ws-vps         -> приватная deploy/config-обертка Amadest/tg-ws-vps
```

Сервис запускает код из `/opt/tg-ws-proxy-core`, но берет systemd-скрипты и настройки из приватной обертки. Ежедневный timer делает:

```text
git pull --ff-only в /opt/tg-ws-vps
git pull --ff-only origin main в /opt/tg-ws-proxy-core
restart tg-ws-proxy, если core изменился
```

Так обновления основной публичной репы не конфликтуют с приватными настройками VPS.

### Ручная установка

Пример для Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
sudo useradd --system --create-home --home-dir /opt/tg-ws-proxy --shell /usr/sbin/nologin tgwsproxy
sudo git clone https://github.com/Flowseal/tg-ws-proxy.git /opt/tg-ws-proxy
sudo chown -R tgwsproxy:tgwsproxy /opt/tg-ws-proxy
sudo -u tgwsproxy python3 -m venv /opt/tg-ws-proxy/.venv
sudo -u tgwsproxy /opt/tg-ws-proxy/.venv/bin/pip install --upgrade pip
sudo -u tgwsproxy /opt/tg-ws-proxy/.venv/bin/pip install cryptography==46.0.5
```

Настройка:

```bash
sudo cp /opt/tg-ws-proxy/deploy/systemd/tg-ws-proxy.env.example /etc/tg-ws-proxy.env
openssl rand -hex 16
sudo nano /etc/tg-ws-proxy.env
sudo cp /opt/tg-ws-proxy/deploy/systemd/tg-ws-proxy.service /etc/systemd/system/tg-ws-proxy.service
sudo chmod +x /opt/tg-ws-proxy/deploy/systemd/run-headless.sh
sudo systemctl daemon-reload
sudo systemctl enable --now tg-ws-proxy
```

Проверка:

```bash
systemctl status tg-ws-proxy
journalctl -u tg-ws-proxy -n 80 --no-pager
ss -lntp | grep 1443
```

## Firewall

Если используется `ufw`:

```bash
sudo ufw allow 1443/tcp
sudo ufw status
```

Если порт закрыт в панели провайдера, его нужно открыть там тоже.

## Когда нужен домен и Nginx

Для простого режима достаточно публичного IP VPS и открытого TCP-порта.

Домен, Nginx и Fake TLS нужны, когда хочется маскировать прокси под обычный TLS-сайт, прокидывать трафик через 443 или прятать сервис за более правдоподобным SNI. В проекте уже есть опции `--fake-tls-domain` и `--proxy-protocol`, а отдельная инструкция лежит в `docs/FakeTlsNginx.md`.
