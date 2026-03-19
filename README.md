> [!CAUTION]
>
> ### Реакция антивирусов
>
> Windows Defender часто ошибочно помечает приложение как **Wacatac**.  
> Если вы не можете скачать из-за блокировки, то:
>
> 1) Попробуйте скачать версию win7 (она ничем не отличается в плане функционала)
> 2) Отключите антивирус на время скачивания, добавьте файл в исключения и включите обратно  
>
> **Всегда проверяйте, что скачиваете из интернета, тем более из непроверенных источников. Всегда лучше смотреть на детекты широко известных антивирусов на VirusTotal**

# TG WS Proxy

**Локальный SOCKS5-прокси** для Telegram Desktop, который **ускоряет работу Telegram**, перенаправляя трафик через WebSocket-соединения. Данные передаются в том же зашифрованном виде, а для работы не нужны сторонние сервера.

<img width="529" height="487" alt="image" src="https://github.com/user-attachments/assets/6a4cf683-0df8-43af-86c1-0e8f08682b62" />

## Как это работает

```
Telegram Desktop → SOCKS5 (127.0.0.1:1080) → TG WS Proxy → WSS → Telegram DC
```

1. Приложение поднимает локальный SOCKS5-прокси на `127.0.0.1:1080`
2. Перехватывает подключения к IP-адресам Telegram
3. Извлекает DC ID из MTProto obfuscation init-пакета
4. Устанавливает WebSocket (TLS) соединение к соответствующему DC через домены Telegram
5. Если WS недоступен (302 redirect) — автоматически переключается на прямое TCP-соединение

## 🚀 Быстрый старт

### Windows

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте **`TgWsProxy_windows.exe`**. Он собирается автоматически через [Github Actions](https://github.com/Flowseal/tg-ws-proxy/actions) из открытого исходного кода.

При первом запуске откроется окно с инструкцией по подключению Telegram Desktop. Приложение сворачивается в системный трей.

**Меню трея:**

- **Открыть в Telegram** — автоматически настроить прокси через `tg://socks` ссылку
- **Перезапустить прокси** — перезапуск без выхода из приложения
- **Настройки...** — GUI-редактор конфигурации
- **Открыть логи** — открыть файл логов
- **Выход** — остановить прокси и закрыть приложение

### macOS

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте **`TgWsProxy_macos_universal.dmg`** — универсальная сборка для Apple Silicon и Intel.

1. Открыть образ
2. Перенести **TG WS Proxy.app** в папку **Applications**
3. При первом запуске macOS может попросить подтвердить открытие: **Системные настройки → Конфиденциальность и безопасность → Всё равно открыть**

### Linux

Для Debian/Ubuntu скачайте со [страницы релизов](https://github.com/Flowseal/tg-ws-proxy/releases) пакет **`TgWsProxy_linux_amd64.deb`**.

Для остальных дистрибутивов можно использовать **`TgWsProxy_linux_amd64`** (бинарный файл для x86_64).

```bash
chmod +x TgWsProxy_linux_amd64
./TgWsProxy_linux_amd64
```

При первом запуске откроется окно с инструкцией. Приложение работает в системном трее (требуется AppIndicator).

### Android

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте подписанный APK вида **`tg-ws-proxy-android-vX.Y.Z.apk`**.

После установки:

- откройте приложение
- проверьте `Android background limits`
- при необходимости отключите battery optimization и снимите background restrictions
- нажмите **Start Service**
- нажмите **Open in Telegram**

Что важно для стабильной работы на Android:

- разрешите уведомления
- отключите battery optimization для приложения

## Установка из исходников

### Консольный proxy

Для запуска только SOCKS5/WebSocket proxy без tray-интерфейса достаточно базовой установки:

```bash
pip install -e .
tg-ws-proxy
```

### Windows 10+

```bash
pip install -e ".[win10]"
tg-ws-proxy-tray-win
```

### Windows 7

```bash
pip install -e ".[win7]"
tg-ws-proxy-tray-win
```

### macOS

```bash
pip install -e ".[macos]"
tg-ws-proxy-tray-macos
```

### Linux

```bash
pip install -e ".[linux]"
tg-ws-proxy-tray-linux
```

### Консольный режим из исходников

```bash
tg-ws-proxy [--port PORT] [--host HOST] [--dc-ip DC:IP ...] [-v]
```

### Android debug APK

Требуются JDK 17, Android SDK и Gradle. Локальная debug-сборка:

```bash
./android/build-local-debug.sh
```

Результат:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

### Android signed release APK

Для локальной release-сборки нужен keystore и переменные окружения:

```bash
export ANDROID_KEYSTORE_FILE=/path/to/tg-ws-proxy-release.keystore
export ANDROID_KEYSTORE_PASSWORD=...
export ANDROID_KEY_ALIAS=tg-ws-proxy
export ANDROID_KEY_PASSWORD=...
```

Сборка:

```bash
cd android
./gradlew assembleRelease
```

Результат:

```text
android/app/build/outputs/apk/release/app-release.apk
```

**Аргументы:**

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `--port` | `1080` | Порт SOCKS5-прокси |
| `--host` | `127.0.0.1` | Хост SOCKS5-прокси |
| `--dc-ip` | `2:149.154.167.220`, `4:149.154.167.220` | Целевой IP для DC (можно указать несколько раз) |
| `-v`, `--verbose` | выкл. | Подробное логирование (DEBUG) |

**Примеры:**

```bash
# Стандартный запуск
tg-ws-proxy

# Другой порт и дополнительные DC
tg-ws-proxy --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# С подробным логированием
tg-ws-proxy -v
```

## CLI-скрипты (pyproject.toml)

CLI команды объявляются в `pyproject.toml` в секции `[project.scripts]` и должны указывать на `module:function`.

Пример:

```toml
[project.scripts]
tg-ws-proxy = "proxy.tg_ws_proxy:main"
tg-ws-proxy-tray-win = "windows:main"
tg-ws-proxy-tray-macos = "macos:main"
tg-ws-proxy-tray-linux = "linux:main"
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

## Настройка Telegram Android

### Автоматически

В приложении нажмите **Open in Telegram** после запуска foreground service.

### Вручную

1. Telegram → **Настройки** → **Данные и память** → **Настройки прокси**
2. Добавить прокси:
   - **Тип:** SOCKS5
   - **Сервер:** `127.0.0.1`
   - **Порт:** `1080`
   - **Логин/Пароль:** оставить пустыми

Важно:

- сначала должен быть запущен foreground service
- если Telegram был уже открыт, иногда проще закрыть и открыть его заново после запуска прокси

## Конфигурация

Tray-приложение хранит данные в:

- **Windows:** `%APPDATA%/TgWsProxy`
- **macOS:** `~/Library/Application Support/TgWsProxy`
- **Linux:** `~/.config/TgWsProxy` (или `$XDG_CONFIG_HOME/TgWsProxy`)

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

## Автоматическая сборка

Проект содержит спецификации PyInstaller ([`packaging/windows.spec`](packaging/windows.spec), [`packaging/macos.spec`](packaging/macos.spec), [`packaging/linux.spec`](packaging/linux.spec)) и GitHub Actions workflow ([`.github/workflows/build.yml`](.github/workflows/build.yml)) для автоматической сборки.

Минимально поддерживаемые версии ОС для текущих бинарных сборок:

- Windows 10+ для `TgWsProxy_windows.exe`
- Windows 7 для `TgWsProxy_windows_7.exe`
- Intel macOS 10.15+
- Apple Silicon macOS 11.0+
- Linux x86_64 (требуется AppIndicator для системного трея)

## Лицензия

[MIT License](LICENSE)
