> [!CAUTION]
>
> ### Реакция антивирусов
> Windows Defender часто ошибочно помечает приложение как **Wacatac**.  
> Если вы не можете скачать из-за блокировки, то:
> 1) Попробуйте скачать версию win7 (она ничем не отличается в плане функционала)
> 2) Отключите антивирус на время скачивания, добавьте файл в исключения и включите обратно  
>
> **Всегда проверяйте, что скачиваете из интернета, тем более из непроверенных источников. Всегда лучше смотреть на детекты широко известных антивирусов на VirusTotal**

# TG WS Proxy

Локальный SOCKS5-прокси для Telegram Desktop, который перенаправляет трафик через WebSocket-соединения к указанным серверам, помогая частично ускорить работу Telegram.  
  
**Ожидаемый результат аналогичен прокидыванию hosts для Web Telegram**: ускорение загрузки и скачивания файлов, загрузки сообщений и части медиа.

<img width="529" height="487" alt="image" src="https://github.com/user-attachments/assets/6a4cf683-0df8-43af-86c1-0e8f08682b62" />

## Как это работает

```
Telegram Desktop → SOCKS5 (127.0.0.1:1080) → TG WS Proxy → WSS (kws*.web.telegram.org) → Telegram DC
```

1. Приложение поднимает локальный SOCKS5-прокси на `127.0.0.1:1080`
2. Перехватывает подключения к IP-адресам Telegram
3. Извлекает DC ID из MTProto obfuscation init-пакета
4. Устанавливает WebSocket (TLS) соединение к соответствующему DC через домены `kws{N}.web.telegram.org`
5. Если WS недоступен (302 redirect) — автоматически переключается на прямое TCP-соединение

## 🚀 Быстрый старт

### Windows
Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте **`TgWsProxy.exe`**. Он собирается автоматически через [Github Actions](https://github.com/Flowseal/tg-ws-proxy/actions) из открытого исходного кода.

При первом запуске откроется окно с инструкцией по подключению Telegram Desktop. Приложение сворачивается в системный трей.

**Linux (окно приложения):**
- Окно остаётся открытым, можно свернуть
- Кнопки: «Открыть в Telegram», «Настройки», «Открыть логи»
- Закрытие окна (×) — полная остановка приложения

## Установка из исходников

```bash
pip install -r requirements.txt
```

### Linux (Debian, Ubuntu)

**Системные зависимости:**
```bash
sudo apt install python3 python3-venv python3-tk
```

**Вариант 1 — установка как приложение** (пункт в меню, окно можно свернуть):
```bash
./packaging/install-linux.sh
```
После установки приложение появится в меню приложений.

**Вариант 2 — запуск из исходников:**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python linux.py
```

**Вариант 3 — сборка standalone-бинарника:**
```bash
./packaging/build-linux.sh
# Результат: dist/tg-ws-proxy
```

### Windows (Tray-приложение)

```bash
python windows.py
```

### Консольный режим (все платформы)

```bash
python proxy/tg_ws_proxy.py [--port PORT] [--dc-ip DC:IP ...] [-v]
```

**Аргументы:**

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `--port` | `1080` | Порт SOCKS5-прокси |
| `--dc-ip` | `2:149.154.167.220`, `4:149.154.167.220` | Целевой IP для DC (можно указать несколько раз) |
| `-v`, `--verbose` | выкл. | Подробное логирование (DEBUG) |

**Примеры:**

```bash
# Стандартный запуск
python proxy/tg_ws_proxy.py

# Другой порт и дополнительные DC
python proxy/tg_ws_proxy.py --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# С подробным логированием
python proxy/tg_ws_proxy.py -v
```

## Настройка Telegram Desktop

### Автоматически

ПКМ по иконке в трее → **«Открыть в Telegram»**

### Вручную

1. Telegram → **Настройки** → **Продвинутые настройки** → **Тип подключения** → **Прокси**
2. Добавить прокси:
   - **Тип:** SOCKS5
   - **Сервер:** `127.0.0.1`
   - **Порт:** `1080`
   - **Логин/Пароль:** оставить пустыми

## Конфигурация

| Платформа | Путь |
|-----------|------|
| Windows | `%APPDATA%/TgWsProxy/config.json` |
| Linux | `~/.config/TgWsProxy/config.json` |

```json
{
  "port": 1080,
  "host": "127.0.0.1",
  "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
  "verbose": false
}
```

## Сборка

| Платформа | Спека | Команда |
|-----------|-------|---------|
| Windows | [`packaging/windows.spec`](packaging/windows.spec) | `pyinstaller packaging/windows.spec` |
| Linux | [`packaging/linux.spec`](packaging/linux.spec) | `./packaging/build-linux.sh` |

GitHub Actions: [`.github/workflows/build.yml`](.github/workflows/build.yml)

## Лицензия

[MIT License](LICENSE)
