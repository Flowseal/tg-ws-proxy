# TG WS Proxy — macOS

macOS-версия [tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy) с нативным menu bar приложением.

## Установка

### Из исходников

Для запуска python3.12 понадобится пакет [HomeBrew](https://brew.sh)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
```bash
brew install python@3.12
```
```bash
# Клонируй репозиторий
git clone https://github.com/Flowseal/tg-ws-proxy
cd tg-ws-proxy

# Перейди в папку с билдом macOS
cd macos

# Создать виртуальное окружение
python3 -m venv venv
# Активировать его
source venv/bin/activate

# Установить зависимости
pip install -r requirements_macos.txt

# Запустить
python macos.py
```

### Сборка .app

```bash
pip install pyinstaller
pyinstaller macos.spec
# Результат: dist/TgWsProxy.app
```
!!! Возможно потребуется единовременное отключение [SIP](https://anonim.xyz/go/https://appstorrent.ru/510-sip.html) для первого запуска

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

## Отличия от Windows-версии

| | Windows | macOS |
|---|---|---|
| GUI-фреймворк | pystray + tkinter | rumps |
| Конфиг | `%APPDATA%\TgWsProxy\` | `~/Library/Application Support/TgWsProxy/` |
| Иконка в трее | Системный трей | Menu bar (строка меню) |
| Сборка | PyInstaller → .exe | PyInstaller → .app |
| Автообновление | - | + |
