# Установка из исходников

## Консольный прокси

Для запуска только прокси без интерфейса системного трея достаточно базовой установки:

```bash
pip install -e .
tg-ws-proxy
```

## Tray-приложение по ОС

### Windows 7/10+

```bash
pip install -e .
tg-ws-proxy-tray-win
```

### macOS

Требуется сборка Python с поддержкой Tk. Проверить её можно командой `python3 -m tkinter`.

```bash
pip install -e .
tg-ws-proxy-tray-macos
```

### Linux

```bash
pip install -e .
tg-ws-proxy-tray-linux
```

## Android

Для локальной сборки нужны JDK 17, Android SDK с платформой 34 и build-tools 34.0.0, Python 3.12 для `standard`, Python 3.11 для `legacy32` и доступ к Gradle/Chaquopy-зависимостям. Из корня репозитория:

```bash
cd android
./gradlew testStandardDebugUnitTest testLegacy32DebugUnitTest \
  assembleStandardDebug assembleLegacy32Debug
```

Debug APK находятся в `android/app/build/outputs/apk/{standard,legacy32}/debug/`. `standard` рассчитан на arm64-v8a/x86_64, `legacy32` на armeabi-v7a. Подписанные APK появляются в [релизах Flowseal](https://github.com/Flowseal/tg-ws-proxy/releases/latest) только при успешной Android release-сборке; если APK в релизе нет, соберите debug APK локально. Для release-сборки требуются `ANDROID_KEYSTORE_FILE`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` и `ANDROID_KEY_PASSWORD`; debug-сборка не требует секретов. Установите подходящий APK, настройте прокси в приложении, запустите его и откройте ссылку подключения в Telegram на том же устройстве.

## Консольный режим из исходников

```bash
tg-ws-proxy [--port PORT] [--host HOST] [--dc-ip DC:IP ...] [-v]
```

**Аргументы:**

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `--port` | `1443` | Порт прокси |
| `--host` | `127.0.0.1` | Хост прокси |
| `--secret` | `random` | 32-значный hex-ключ для авторизации клиентов |
| `--dc-ip` | `2:149.154.167.220`, `4:149.154.167.220` | Целевой IP для DC (параметр можно указывать несколько раз) |
| `--no-cfproxy` | `false` | Отключить попытку [проксирования через Cloudflare](./CfProxy.md) |
| `--cfproxy-domain` | | Указать свой домен для проксирования через Cloudflare [Подробнее](./CfProxy.md). Можно указать несколько через повторение аргумента. |
| `--cfproxy-worker-domain` | | Домен Cloudflare Worker [Подробнее](./CfWorker.md). Можно указать несколько через повторение аргумента. |
| `--no-secure` | `false` | Принудительно использует 80 порт для CF-proxy и CF-worker |
| `--fake-tls-domain` | | Включить маскировку Fake TLS (ee-secret) с указанным SNI-доменом |
| `--proxy-protocol` | выкл. | Принимать HAProxy PROXY protocol v1 (для работы за nginx/haproxy с `proxy_protocol on`) |
| `--buf-kb` | `256` | Размер буфера в КБ |
| `--pool-size` | `4` | Количество заготовленных соединений на каждый DC |
| `--log-file` | выкл. | Путь к файлу, в который будут сохраняться логи |
| `--log-max-mb` | `5` | Максимальный размер файла логов в МБ (после этого начинается перезапись) |
| `--log-backups` | `0` | Количество сохранений логов после перезаписи |
| `-v`, `--verbose` | выкл. | Подробное логирование (DEBUG) |

**Примеры:**

```bash
# Стандартный запуск
tg-ws-proxy --secret 00112233445566778899aabbccddeeff

# Другой порт и дополнительные DC
tg-ws-proxy --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# С подробным логированием, без прямого подключения к DC
tg-ws-proxy -v --dc-ip

# Fake TLS маскировка (ee-secret)
tg-ws-proxy --fake-tls-domain example.com
```
