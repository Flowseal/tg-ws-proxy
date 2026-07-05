# TG WS Proxy для macOS

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте `TgWsProxy_macos_universal.dmg` (универсальная сборка для Apple Silicon и Intel).

1. Откройте образ
2. Перенесите `TG WS Proxy.app` в папку `Applications`
3. При первом запуске macOS может попросить подтвердить открытие: **Системные настройки → Конфиденциальность и безопасность → Всё равно открыть**

Минимально поддерживаемые версии:

- Intel macOS 10.15+
- Apple Silicon macOS 11.0+

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
tg-ws-proxy-tray-macos
```
