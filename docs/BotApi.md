# Telegram Bot API

Опциональный прозрачный туннель для `api.telegram.org` — чтобы **Telegram-боты** (aiogram, python-telegram-bot и др.) работали без VPN и без правок кода.

> По умолчанию **выключено**. На пользователей Telegram Desktop / MTProto не влияет.

## Как это работает

```
Бот  →  api.telegram.org:443
              ↓  hosts → 127.0.0.2
Локальный TCP :443  →  Cloudflare Worker /apiws?dst=api.telegram.org  →  Telegram
```

- TLS **end-to-end** — прокси не расшифровывает трафик
- Код бота не меняется (не нужен `base_url` / кастомный session)
- Используется тот же Worker, что и для MTProto ([CfWorker.md](./CfWorker.md)), путь `/apiws`
- Слушаем `127.0.0.2:443` (не `127.0.0.1`), чтобы реже конфликтовать с другими сервисами на localhost

## Включение

1. Укажите домен Cloudflare Worker (настройки / `--cfproxy-worker-domain`)
2. Включите **Telegram Bot API** в настройках (или флаг `--bot-api`)
3. Запустите приложение **от администратора** (правка `hosts` + порт `443`)
4. Запускайте ботов как обычно

| Параметр | По умолчанию | Описание |
| --- | --- | --- |
| `bot_api` / `--bot-api` / `--no-bot-api` | `off` | Включить прозрачный туннель |

При остановке приложения блок в `hosts` снимается автоматически  
(`# tg-ws-proxy-botapi-begin` … `# tg-ws-proxy-botapi-end`).

## Ограничения

- Нужен Cloudflare Worker с `/apiws` (как в [CfWorker.md](./CfWorker.md))
- Нужны права администратора на время работы режима
- Пока режим включён, весь трафик машины к `api.telegram.org` идёт через туннель
- Рассчитано на desktop/tray; в Docker — см. [README.docker.md](./README.docker.md)

## Для мейнтейнеров

Сейчас Worker задаёт пользователь (как и для MTProto fallback).

Чтобы получить UX «скачал → боты работают» без ручной настройки Worker, можно позже добавить **автопул доменов** по аналогии с CF Proxy (`.github/cfproxy-domains.txt`): встроенный список + периодический refresh. Клиентская часть туннеля к этому уже готова — достаточно источника доменов.
