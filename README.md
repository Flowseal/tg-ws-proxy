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

## Установка

### Fedora (GNOME)

Для установки в Linux с GNOME выполните:

### Ubuntu/Debian:

```bash
# Установите зависимости 
sudo apt install libappindicator3-1 python3-tk xclip

# Запустите скрипт установки
./install.sh
```
### Fedora / RHEL:

```bash
# Установите зависимости 
sudo dnf install python3-pip python3-tkinter libappindicator-gtk3

# Запустите скрипт установки
./install.sh
```

После установки приложение появится в меню приложений и будет доступно по команде `tg-ws-proxy`.

**Для удаления:**
```bash
./uninstall.sh
```

### Из исходников (кроссплатформенно)

```bash
pip install -r requirements.txt
```

## Использование

### Tray-приложение (Linux/Windows)

```bash
python tg_ws_tray.py
```

При первом запуске откроется окно с инструкцией по подключению Telegram Desktop. Приложение сворачивается в системный трей.

**Меню трея:**
- **Открыть в Telegram** — автоматически настроить прокси через `tg://socks` ссылку
- **Перезапустить прокси** — перезапуск без выхода из приложения
- **Настройки...** — GUI-редактор конфигурации
- **Открыть логи** — открыть файл логов
- **Выход** — остановить прокси и закрыть приложение

### Консольный режим

```bash
python tg_ws_proxy.py [--port PORT] [--dc-ip DC:IP ...] [-v]
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
python tg_ws_proxy.py

# Другой порт и дополнительные DC
python tg_ws_proxy.py --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# С подробным логированием
python tg_ws_proxy.py -v
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

Tray-приложение хранит конфигурацию в:
- **Windows:** `%APPDATA%/TgWsProxy/config.json`
- **Linux:** `~/.config/tgwsproxy/config.json`

Пример конфигурации:

```json
{
  "port": 1080,
  "dc_ip": [
    "2:149.154.167.220",
    "4:149.154.167.220"
  ],
  "verbose": false
}
```

Логи записываются в:
- **Windows:** `%APPDATA%/TgWsProxy/proxy.log`
- **Linux:** `~/.config/tgwsproxy/proxy.log`

## Сборка exe

Проект содержит спецификацию PyInstaller ([`tg_ws_proxy.spec`](tg_ws_proxy.spec)) и GitHub Actions workflow ([`.github/workflows/build.yml`](.github/workflows/build.yml)) для автоматической сборки.

```bash
pip install pyinstaller
pyinstaller tg_ws_proxy.spec
```

## Требования для Linux

Для работы tray-приложения в GNOME требуется:

```bash
sudo dnf install libappindicator-gtk3 python3-tkinter xclip
```

## Дисклеймер
Проект частично vibecoded by Opus 4.6. Если вы найдете баг, то создайте Issue с его описанем.

## Лицензия

[MIT License](LICENSE)
