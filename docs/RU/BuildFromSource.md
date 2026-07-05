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

```bash
pip install -e .
tg-ws-proxy-tray-macos
```

### Linux

```bash
pip install -e .
tg-ws-proxy-tray-linux
```

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
tg-ws-proxy

# Другой порт и дополнительные DC
tg-ws-proxy --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# С подробным логированием
tg-ws-proxy -v

# Fake TLS маскировка (ee-secret)
tg-ws-proxy --fake-tls-domain example.com
```
