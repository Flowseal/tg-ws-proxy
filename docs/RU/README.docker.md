# TG WS Proxy для Docker

## Готовый Docker Compose с образами из Docker Hub

Рекомендуемый вариант для VPS: [три готовых образа и статический Compose](./DockerHub.md).
На сервер копируются только `docker-compose.yaml`, `config.toml` и `.env`; Python и
сборка на VPS не нужны. Три Dockerfile находятся в `deploy/docker/`.

```bash
docker compose pull
docker compose up -d --wait --wait-timeout 120
docker compose exec -T proxy python /app/deploy/docker/entrypoint.py link
```

Перед запуском задайте namespace/tag образов в `.env`, адрес VPS и SNI в TOML.
Ниже описан прежний вариант с Python-установщиком и сборкой из исходников.

## Автоматический запуск на VPS с HTTPS-маскировкой

Установите Docker Engine с Compose v2, Python 3.8+ и OpenSSL, скачайте
репозиторий и выполните из его корня:

```bash
python3 deploy/setup_masking.py --mode docker --domain proxy.example.com
```

Замените домен на свой, направленный на VPS. Публичный порт 443 должен быть
свободен. Команда создаст сайт, самоподписанный сертификат, постоянный secret,
Compose-конфигурацию и запустит nginx + прокси. После проверки HTTPS через
прокси будет выведена `ee`-ссылка. Существующий доверенный сертификат можно
подключить параметрами `--cert /path/fullchain.pem --key /path/privkey.pem`.
Самоподписанный сертификат остаётся узнаваемым как самоподписанный.

Состояние сохраняется в `.tgws-masking/`. Повторный запуск с теми же параметрами
сохраняет secret, сертификат и содержимое сайта. Для другого каталога
используйте `--state-dir`, для другого публичного порта — `--port 2443`.
Для подготовки файлов без запуска контейнеров добавьте `--prepare-only`.

```bash
docker compose -f .tgws-masking/compose.yaml ps
docker compose -f .tgws-masking/compose.yaml logs proxy
docker compose -f .tgws-masking/compose.yaml down
```

Наружу открыт только прокси; nginx находится во внутренней сети. TLS-ключ
монтируется только в nginx. `down` не удаляет файлы состояния. Установщик
собирает образ и ждёт healthcheck, проверяющий настоящий HTTPS через masking.
TCPMSS=88 настраивается внутри контейнера при каждом старте до сброса прав
до `app`. Для этого Compose кратковременно использует root + NET_ADMIN с
no-new-privileges; firewall хоста не меняется. Значение задаётся через
`--tcpmss 92`, а `--no-tcpmss` пересоздаёт контейнер без bootstrap и capability.
Для rootless Docker или ядра без TCPMSS может потребоваться отключение.
Docker Desktop/userland forwarding могут завершать внешнее TCP-соединение
вне этого namespace: проверяйте MSS со стороны клиента на Linux VPS.
nfqws автоматически собирается из zapret v72.12 в отдельный образ и запускается
с NET_ADMIN/NET_RAW. Прокси использует его сетевое пространство; порт публикует
контейнер nfqws. Правила NFQUEUE применяются до Docker NAT и восстанавливаются
при старте. Healthcheck проверяет привязку обработчика к очереди 200.
`--nfqws-ttl 8` меняет TTL, `--no-nfqws` удаляет обработчик при повторном запуске
установщика. Для rootless Docker отключите оба механизма:
`--no-tcpmss --no-nfqws`. Обычный `docker run` сохраняет прежнее поведение;
автоматический nfqws включается через установщик Compose. Полное описание:
[Защита клиент ↔ VPS](./ClientDpi.md).

Anti-replay работает автоматически в оперативной памяти: до 8192 записей,
окно 241 секунда. Кеш очищается при перезапуске процесса; отдельный том
или база данных не нужны.

## Установка из исходников

Вводите команды последовательно, одну за другой:

```bash
# Скачиваем репозиторий
git clone https://github.com/Flowseal/tg-ws-proxy.git

# Переходим в папку с проектом
cd tg-ws-proxy

# Собираем образ
docker build -t tg-ws-proxy .

# Запускаем контейнер
docker run -d \
  --name tg-ws-proxy \
  --restart=always \
  -p 1443:1443 \
  tg-ws-proxy:latest

# Получаем ссылку для подключения
docker logs tg-ws-proxy 2>&1 | grep 'tg://proxy'
```

После выполнения последней команды вы увидите ссылку вида:

```text
tg://proxy?server=172.17.0.2&port=1443&secret=dd68f127db1d...
```

## Настройка параметров

Настройки задаются переменными окружения при запуске контейнера. Дополнительные
CLI-параметры можно передать после имени образа:

| Переменная              | Описание                                                      | По умолчанию                          |
| ----------------------- | ------------------------------------------------------------- | ------------------------------------- |
| `TG_WS_PROXY_HOST`      | `Адрес для приёма подключений`                                | `0.0.0.0`                             |
| `TG_WS_PROXY_PORT`      | `Порт внутри контейнера`                                      | `1443`                                |
| `TG_WS_PROXY_SECRET`    | `Секретный ключ`                                              | `random`                              |
| `TG_WS_PROXY_DC_IPS`    | `Пары «номер DC:IP» через пробел`                             | `2:149.154.167.220 4:149.154.167.220` |
| `TG_WS_PROXY_CF_WORKER` | `Домен Cloudflare Worker`                                     | `None`                                |
| `TG_WS_PROXY_NO_SECURE` | `Использовать 80 порт для подключения к CF-proxy и CF-Worker` | `False`                               |
| `TG_WS_PROXY_FAKE_TLS_DOMAIN` | `Включить Fake TLS с указанным SNI` | пусто |
| `TG_WS_PROXY_FAKE_TLS_MASK_HOST` | `HTTPS backend; в автоматическом Compose — cover` | домен SNI |
| `TG_WS_PROXY_FAKE_TLS_MASK_PORT` | `Порт HTTPS backend` | `443` |
| `TG_WS_PROXY_FAKE_TLS_DRS` | `DRS при включённом Fake TLS` | `true` |
| `TG_WS_PROXY_FAKE_TLS_SPLIT` | `Раздельная отправка ServerHello` | `true` |
| `TG_WS_PROXY_FAKE_TLS_SPLIT_DELAY_MS` | `Базовая задержка разделения` | `5` |
| `TG_WS_PROXY_FAKE_TLS_SPLIT_JITTER_MS` | `Максимальная добавочная задержка` | `10` |
| `TG_WS_PROXY_CF_DOMAIN` | `Домен CF proxy` | авто |
| `TG_WS_PROXY_POOL_SIZE` | `Размер WS-пула на DC` | `4` |
| `TG_WS_PROXY_NO_H2` | `Отключить HTTP/2 для CF media` | `false` |
| `TG_WS_PROXY_NO_CFPROXY` | `Отключить CF proxy` | `false` |
| `TG_WS_PROXY_TCPMSS` | `Настроить TCPMSS до сброса прав до app` | `false`; установщик задаёт `true` |
| `TG_WS_PROXY_TCPMSS_MSS` | `Объявляемый MSS при включённом bootstrap` | `88` |

Булевы значения поддерживают `true/false`, `1/0`, `yes/no`, `on/off` без учёта
регистра. Сам по себе `TG_WS_PROXY_FAKE_TLS_DOMAIN` не создаёт backend в обычном
`docker run`: используйте автоматический установщик или подготовьте backend.
Внутри Docker `127.0.0.1` означает текущий контейнер, поэтому для отдельного
nginx используйте его имя в общей сети.

При ручном `docker run` с TCPMSS нужны `--user 0:0 --cap-add NET_ADMIN
--security-opt no-new-privileges=true -e TG_WS_PROXY_TCPMSS=true`. Обычный образ
по умолчанию работает от `app` без bootstrap. Ошибка прав firewall останавливает
запуск вместо молчаливого запуска незащищённого слушателя. IPv4 проверяется;
недоступность IPv6 явно выводится в лог.

Пример с ручным указанием секрета:

```bash
docker run -d \
  --name tg-ws-proxy \
  --restart=always \
  -p 1443:1443 \
  -e TG_WS_PROXY_SECRET="ваш_секрет" \
  tg-ws-proxy:latest
```

Для генерации секрета можно использовать:

 ```bash
openssl rand -hex 16  
```

## Настройка Telegram Desktop

1. Telegram → **Настройки** → **Продвинутые настройки** → **Тип подключения** → **Прокси**
2. Добавьте прокси:
   - **Тип:** MTProto
   - **Сервер:** публичный IP/домен VPS; `127.0.0.1` только при локальном Docker
   - **Порт:** опубликованный порт Docker (`1443` в ручном примере, `443` у установщика)
   - **Secret:** из настроек или логов
