> [!TIP]
>
> ### [🎉 Поддержать меня](https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/Funding.md)
>
> **USDT (TRC20)**: `TXPnKs2Ww1RD8JN6nChFUVmi5r2hqrWjuu`  
> **BTC**: `bc1qr8vd6jelkyyry3m4mq6z5txdx4pl856fu6ss0w`  
> **ETH**: `0x1417878fdc5047E670a77748B34819b9A49C72F1`  
> **Другие монеты**: https://nowpayments.io/donation/flowseal

> [!CAUTION]
>
> ### Реакция антивирусов
>
> Антивирусы часто ошибочно помечают приложение как вирус из-за упаковщика.  
> Если вы не можете скачать из-за блокировки антивирусом, то:
>
> 1) **Попробуйте скачать версию win7 (она ничем не отличается в плане функционала)**
> 2) Отключите антивирус на время скачивания, добавьте файл в исключения и включите обратно  
>
> Всегда проверяйте, что скачиваете из интернета, тем более из непроверенных источников. Всегда лучше смотреть на детекты широко известных антивирусов на VirusTotal

# TG WS Proxy

**Локальный MTProto-прокси** для Telegram Desktop, который **ускоряет работу Telegram**, перенаправляя трафик через WebSocket-соединения. Данные передаются в том же зашифрованном виде, а для работы не нужны сторонние сервера.

<img width="529" height="487" alt="image" src="https://github.com/user-attachments/assets/6a4cf683-0df8-43af-86c1-0e8f08682b62" />

## Как это работает

```
Telegram Desktop → MTProto Proxy (127.0.0.1:1443) → WebSocket → Telegram DC
```

1. Приложение поднимает MTProto прокси на `127.0.0.1:1443`
2. Перехватывает подключения к IP-адресам Telegram
3. Извлекает DC ID из MTProto obfuscation init-пакета
4. Устанавливает WebSocket (TLS) соединение к соответствующему DC через домены Telegram
5. Если WS недоступен (302 redirect) — автоматически переключается на CfProxy / прямое TCP-соединение

> [!IMPORTANT] 
> ### Не грузит фото/видео?
> **Удалите в настройках прокси в DC->IP всё, кроме `4:149.154.167.220`**  
> **Если не помогло, то удалите вообще всё из этого поля**  
> ####
> Подобная проблема встречается на аккаунтах без Premium  
> Если вам не помогло, то настраивайте свой домен по гайду отсюда: https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/CfProxy.md

## 🚀 Быстрый старт

### Windows

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте **`TgWsProxy_windows.exe`**. Он собирается автоматически через [Github Actions](https://github.com/Flowseal/tg-ws-proxy/actions) из открытого исходного кода.

При первом запуске откроется окно с инструкцией по подключению Telegram Desktop. Приложение сворачивается в системный трей.

**Меню трея:**

- **Открыть в Telegram** — автоматически настроить прокси через `tg://proxy` ссылку
- **Скопировать ссылку** — скопировать ссылку для подключения
- **Перезапустить прокси** — перезапуск без выхода из приложения
- **Настройки...** — GUI-редактор конфигурации (в т.ч. версия приложения, опциональная проверка обновлений с GitHub)
- **Открыть логи** — открыть файл логов
- **Выход** — остановить прокси и закрыть приложение

При первом запуске после старта может появиться запрос об открытии страницы релиза, если на GitHub вышла новая версия (отключается в настройках).

### Настройка Telegram Desktop

### Автоматически:

ПКМ по иконке в трее → **«Открыть в Telegram»**  
Если не сработало (не открылся Telegram с подключением), то:
1. ПКМ по иконке в трее → **«Скопировать ссылку»** 
2. Отправьте ссылку себе в избранное в Telegram клиенте и нажмите по ней ЛКМ
3. Подключитесь

### Вручную:

1. Telegram → **Настройки** → **Продвинутые настройки** → **Тип подключения** → **Прокси**
2. Добавить прокси:
   - **Тип:** MTProto
   - **Сервер:** `127.0.0.1` (или переопределенный вами)
   - **Порт:** `1443` (или переопределенный вами)
   - **Secret:** из настроек или логов

##
### macOS

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте **`TgWsProxy_macos_universal.dmg`** — универсальная сборка для Apple Silicon и Intel.

1. Открыть образ
2. Перенести **TG WS Proxy.app** в папку **Applications**
3. При первом запуске macOS может попросить подтвердить открытие: **Системные настройки → Конфиденциальность и безопасность → Всё равно открыть**

### Linux

Для Debian/Ubuntu скачайте со [страницы релизов](https://github.com/Flowseal/tg-ws-proxy/releases) пакет **`TgWsProxy_linux_amd64.deb`**.

Для Arch и Arch-Based дистрибутивов подготовлены пакеты в AUR: [tg-ws-proxy-bin](https://aur.archlinux.org/packages/tg-ws-proxy-bin), [tg-ws-proxy-git](https://aur.archlinux.org/packages/tg-ws-proxy-git), [tg-ws-proxy-cli](https://aur.archlinux.org/packages/tg-ws-proxy-cli)

```shell
# Установка без AUR-helper
git clone https://aur.archlinux.org/tg-ws-proxy-bin.git
cd tg-ws-proxy-bin
makepkg -si

# При помощи AUR-helper
paru -S tg-ws-proxy-bin

# Если вы установили -cli пакет, то запуск осуществляется через systemctl, где 8888 это номер порта,
# разделитель ":" и secret, который можно сгенерировать командой: openssl rand -hex 16
sudo systemctl start tg-ws-proxy-cli@8888:3075abe65830f0325116bb0416cadf9f
```

Для остальных дистрибутивов можно использовать **`TgWsProxy_linux_amd64`** (бинарный файл для x86_64).

```bash
chmod +x TgWsProxy_linux_amd64
./TgWsProxy_linux_amd64
```

При первом запуске откроется окно с инструкцией. Приложение работает в системном трее (требуется AppIndicator).

## Установка из исходников

### Консольный proxy

Для запуска только proxy без tray-интерфейса достаточно базовой установки:

```bash
pip install -e .
tg-ws-proxy
```

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

### Консольный режим из исходников

```bash
tg-ws-proxy [--port PORT] [--host HOST] [--dc-ip DC:IP ...] [-v]
```

**Аргументы:**

| Аргумент | По умолчанию | Описание |
|---|---|---|
| `--port` | `1443` | Порт прокси |
| `--host` | `127.0.0.1` | Хост прокси |
| `--secret` | `random` | 32 hex chars secret для авторизации клиентов |
| `--dc-ip` | `2:149.154.167.220`, `4:149.154.167.220` | Целевой IP для DC (можно указать несколько раз) |
| `--no-cfproxy` | `false` | Отключить попытку [проксирования через Cloudflare]((https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/CfProxy.md)) |
| `--cfproxy-domain` | | Указать свой домен для проксирования через Cloudflare. [Подробнее тут](https://github.com/Flowseal/tg-ws-proxy/blob/main/docs/CfProxy.md) |
| `--cfproxy-priority` | `true` | Пробовать проксировать через Cloudflare перед прямым TCP подключением |
| `--fake-tls-domain` | | Включить Fake TLS (ee-secret) маскировку с указанным SNI-доменом |
| `--proxy-protocol` | выкл. | Принимать HAProxy PROXY protocol v1 (для работы за nginx/haproxy с `proxy_protocol on`) |
| `--buf-kb` | `256` | Размер буфера в КБ |
| `--pool-size` | `4` | Количество заготовленных соединений на каждый DC |
| `--log-file` | выкл. | Путь до файла, в который сохранять логи  |
| `--log-max-mb` | `5` | Максимальный размер файла логов в МБ (после идёт перезапись) |
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

## Fake TLS + nginx upstream
### Домен (`--fake-tls-domain`) должен указывать на тот же IP, на котором стоит прокси

**Пример `nginx.conf` (stream):**

```nginx
upstream mtproto {
    server 127.0.0.1:8446;
}

map $ssl_preread_server_name $sni_name {
    hostnames;
    example.com mtproto;
    # if you have xray with selfsni running:
    # sub.example.com  www;
    # default xray;
}

# upstream xray {
#     server 127.0.0.1:8443;
# }
# 
# upstream www {
#     server 127.0.0.1:7443;
# }

server {
    proxy_protocol on;
    set_real_ip_from unix:;
    listen          443;
    proxy_pass      $sni_name;
    ssl_preread     on;
}
```

**Запуск прокси за nginx:**

```bash
python3 proxy/tg_ws_proxy.py \
  --port 8446 \
  --host 127.0.0.1 \
  --fake-tls-domain example.com \
  --proxy-protocol \
  --secret <32-hex-chars>
```

Ссылка для подключения будет в формате `ee`-секрета:</p>

```
tg://proxy?server=your.domain.com&port=443&secret=ee<secret><domain_hex>
```

## Файлы конфигурации Tray-приложения

Tray-приложение хранит данные в:

- **Windows:** `%APPDATA%/TgWsProxy`
- **macOS:** `~/Library/Application Support/TgWsProxy`
- **Linux:** `~/.config/TgWsProxy` (или `$XDG_CONFIG_HOME/TgWsProxy`)

```json
{
  "host": "127.0.0.1",
  "port": 1443,
  "secret": "...",
  "dc_ip": [
    "2:149.154.167.220",
    "4:149.154.167.220"
  ],
  "verbose": false,
  "buf_kb": 256,
  "pool_size": 4,
  "log_max_mb": 5.0,
  "check_updates": true,
  "cfproxy": true,
  "cfproxy_priority": true,
  "cfproxy_user_domain": "",
  "appearance": "auto"
}
```

Ключ **`check_updates`** — при `true` при запросе к GitHub сравнивается версия с последним релизом (только уведомление и ссылка на страницу загрузки). На Windows в конфиге может быть **`autostart`** (автозапуск при входе в систему).

## Автоматическая сборка

Проект содержит спецификации PyInstaller ([`packaging/windows.spec`](../packaging/windows.spec), [`packaging/macos.spec`](../packaging/macos.spec), [`packaging/linux.spec`](../packaging/linux.spec)) и GitHub Actions workflow ([`.github/workflows/build.yml`](../.github/workflows/build.yml)) для автоматической сборки.

Минимально поддерживаемые версии ОС для текущих бинарных сборок:

- Windows 10+ для `TgWsProxy_windows.exe`
- Windows 7 (x64) для `TgWsProxy_windows_7_64bit.exe`
- Windows 7 (x32) для `TgWsProxy_windows_7_32bit.exe`
- Intel macOS 10.15+
- Apple Silicon macOS 11.0+
- Linux x86_64 (требуется AppIndicator для системного трея)

## Лицензия

[MIT License](https://github.com/Flowseal/tg-ws-proxy/blob/main/LICENSE)
