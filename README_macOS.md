# TG WS Proxy — macOS

macOS-версия [tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy) с нативным menu bar приложением.

## Как это работает

```
Telegram Desktop → SOCKS5 (127.0.0.1:1080) → TG WS Proxy → WSS (kws*.web.telegram.org) → Telegram DC
```

Полный функциональный паритет с Windows-версией:
- Локальный SOCKS5-прокси
- Автоматическое переключение WebSocket → TCP fallback
- GUI-настройки
- Просмотр логов
- Одна копия через lock-файл
- Первый запуск с инструкцией

## Установка

### Из исходников

```bash
# Клонируй оригинальный репозиторий
git clone https://github.com/Flowseal/tg-ws-proxy
cd tg-ws-proxy

# Скопируй файлы из этого порта в репозиторий:
# macos.py, macos.spec, requirements_macos.txt

# Установи зависимости
pip install -r requirements_macos.txt

# Запуск
python macos.py
```

### Сборка .app

```bash
pip install pyinstaller
pyinstaller macos.spec
# Результат: dist/TgWsProxy.app
```

## GUI

Приложение живёт в menu bar (строка меню вверху экрана). Нет иконки в Dock.

**Меню:**
- **Открыть в Telegram** — откроет `tg://socks?...` ссылку, Telegram сам добавит прокси
- **Перезапустить прокси** — горячий перезапуск
- **Настройки…** — окно с полями Host, Port, DC IPs, Verbose
- **Открыть логи** — откроет файл логов в TextEdit
- **Выход** — остановить прокси и закрыть

## Конфигурация

Хранится в `~/Library/Application Support/TgWsProxy/config.json`:

```json
{
  "port": 1080,
  "host": "127.0.0.1",
  "dc_ip": [
    "2:149.154.167.220",
    "4:149.154.167.220"
  ],
  "verbose": false
}
```

Логи: `~/Library/Application Support/TgWsProxy/proxy.log`

## Настройка Telegram Desktop

### Автоматически
Нажми **«Открыть в Telegram»** в меню строки меню.

### Вручную
1. Telegram → **Настройки** → **Продвинутые настройки** → **Тип подключения** → **Прокси**
2. Добавь прокси:
   - **Тип:** SOCKS5
   - **Сервер:** `127.0.0.1`
   - **Порт:** `1080`
   - **Логин/Пароль:** пусто

## Зависимости

| Библиотека | Назначение |
|-----------|------------|
| `rumps` | macOS menu bar framework |
| `cryptography` | MTProto obfuscation (из оригинала) |
| `psutil` | Проверка запущенных копий |
| `tkinter` | GUI окна (входит в стандартный Python) |

## Отличия от Windows-версии

| | Windows | macOS |
|---|---|---|
| GUI-фреймворк | pystray + tkinter | rumps + tkinter |
| Конфиг | `%APPDATA%\TgWsProxy\` | `~/Library/Application Support/TgWsProxy/` |
| Иконка в трее | Системный трей | Menu bar (строка меню) |
| Сборка | PyInstaller → .exe | PyInstaller → .app |
