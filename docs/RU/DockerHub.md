# Готовые образы и запуск через Docker Compose

На VPS нужны только Docker Engine и Compose v2. Python, Git, компилятор и
`deploy/setup_masking.py` на сервере не требуются. Корневой `docker-compose.yaml`
использует только `image:`, без `build:`.

## Сборка и публикация

Из корня репозитория на машине сборки:

```bash
export DOCKERHUB_NAMESPACE=ваш_логин_dockerhub
export IMAGE_TAG=vps-1
docker login
```

Три независимых Dockerfile:

| Образ | Dockerfile | Назначение |
|---|---|---|
| `tg-ws-proxy` | `deploy/docker/proxy.Dockerfile` | Прокси от UID/GID 10001, без capabilities |
| `tg-ws-proxy-cover` | `deploy/docker/cover.Dockerfile` | Сайт, сертификат, постоянный secret и конфигурация |
| `tg-ws-proxy-nfqws` | `deploy/docker/nfqws.Dockerfile` | Общая сеть с прокси, TCPMSS и NFQUEUE |

Для публикации сразу amd64 и arm64 используйте
[Docker Buildx](https://docs.docker.com/reference/cli/docker/buildx/build/). Это позволяет собирать
на Mac ARM и запускать на x86 VPS. Создание builder выполняется один раз:

```bash
docker buildx create --name tgws-builder --use
docker buildx inspect --bootstrap

docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/proxy.Dockerfile \
  -t "$DOCKERHUB_NAMESPACE/tg-ws-proxy:$IMAGE_TAG" --push .

docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/cover.Dockerfile \
  -t "$DOCKERHUB_NAMESPACE/tg-ws-proxy-cover:$IMAGE_TAG" --push .

docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/nfqws.Dockerfile \
  -t "$DOCKERHUB_NAMESPACE/tg-ws-proxy-nfqws:$IMAGE_TAG" --push .
```

Используйте одинаковый тег для всех трёх образов. Для локальной проверки одной
архитектуры замените `--push` на `--load` и оставьте одну платформу.
Скрипт не публикует образы автоматически: сборку и push выполняет владелец.

## Файлы на VPS

После публикации образов и файлов репозитория скопируйте на VPS:

- `docker-compose.yaml`;
- `config.toml.example`, переименовав в `config.toml`;
- `.env.example`, переименовав в `.env` (или задайте переменные окружения сами).

Для текущей ветки пример скачивания выглядит так:

```bash
mkdir -p ~/tg-ws-proxy
cd ~/tg-ws-proxy

TGWS_BASE=https://raw.githubusercontent.com/alex98247/tg-ws-proxy/feature/vps
curl -fL "$TGWS_BASE/docker-compose.yaml" -o docker-compose.yaml
curl -fL "$TGWS_BASE/config.toml.example" -o config.toml
curl -fL "$TGWS_BASE/.env.example" -o .env

nano .env
nano config.toml
```

При использовании другой ветки или release tag замените `feature/vps` в URL.

В `.env` укажите Docker Hub namespace и опубликованный тег:

```dotenv
DOCKERHUB_NAMESPACE=ваш_логин_dockerhub
IMAGE_TAG=vps-1
PUBLIC_PORT=443
PUBLIC_BIND_ADDRESS=0.0.0.0
```

`.env` управляет образами и публикацией порта Docker. TOML читают контейнеры;
Docker Compose не умеет получать параметры публикации портов из TOML.
Поэтому `server.port` и `server.bind_address` в этой схеме не меняют внешний
порт и интерфейс: используйте `PUBLIC_PORT` и `PUBLIC_BIND_ADDRESS`.
Внутри общей сети прокси всегда слушает `0.0.0.0:1443`.
`server.public_port`, если задан, меняет только порт в Telegram-ссылке (NAT);
иначе в ссылке используется `PUBLIC_PORT`.

В `config.toml` измените `server.public_host` на IP или домен VPS, а
`fake_tls.domain` — на выбранный SNI. Для локального сайта предпочтительно
использовать свой домен с DNS-записью на VPS. По умолчанию включены DRS,
ServerHello split, TCPMSS=88, nfqws с TTL 6 и anti-replay в памяти.
`access.secret` можно оставить закомментированным: контейнер сам создаст и
сохранит его. Конфиг можно хранить с правами `600`: его читает только bootstrap
контейнера cover, слушатель прокси не получает host-файл.

## Первый запуск

Убедитесь, что TCP `PUBLIC_PORT` свободен и открыт в firewall панели VPS:

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 120
docker compose ps
```

Если Docker доступен только root, добавляйте `sudo` к Docker-командам.
Приватную ссылку для Telegram можно получить без чтения внутреннего JSON:

```bash
docker compose exec -T proxy python /app/deploy/docker/entrypoint.py link
```

`cover` готовит состояние, затем запускается сетевой контейнер и только после
их healthcheck — прокси. TCPMSS и NFQUEUE настраиваются в сетевом namespace
контейнера, firewall хоста не меняется. Прокси работает без root/NET_ADMIN.
Наружу публикуется только один порт; сайт доступен через masking прокси.
Контейнеры автоматически поднимаются после перезагрузки Docker/VPS.
Схема рассчитана на обычный rootful Docker Engine на Linux.

## Маскировка, сертификаты и переключатели

При `masking.mode = "local"` cover создаёт сайт и самоподписанный сертификат
для `fake_tls.domain`. Сертификат и сайт сохраняются в томе. Автоматического
ACME и продления доверенных сертификатов нет.

Для своего сертификата создайте рядом с Compose каталог `certs/`, положите
туда `fullchain.pem` и `privkey.pem`, раскомментируйте mount `./certs` у cover
и задайте в TOML:

```toml
[masking]
mode = "local"
port = 8443
certificate = "certs/fullchain.pem"
private_key = "certs/privkey.pem"
```

После внешнего продления сертификата пересоздайте контейнеры. Приватный ключ
не монтируется в прокси или nfqws.

При `masking.mode = "external"` задайте `masking.port = 443` и при
необходимости `backend_host`; удалите настройки локального сертификата.
Три сервиса сохраняются: cover подготавливает secret/настройки и ожидает,
nginx в нём не запускается. Пробы получает внешний HTTPS-backend.

`deployment.tcpmss = 0` отключает TCPMSS. `deployment.nfqws = false` отключает
обработчик очереди; его контейнер остаётся владельцем сети и, при необходимости,
поддерживает TCPMSS. `deployment.nfqws_ttl` меняет TTL. Внешние сетевые
capabilities остаются только у сетевого контейнера. `deployment.mode` и
`deployment.state_dir` относятся к старому установщику и в готовом Compose
не используются.

## Изменения, обновление и сохранность ссылок

После изменения TOML или `.env` пересоздавайте весь проект, чтобы cover
подготовил новую конфигурацию для остальных контейнеров:

```bash
docker compose up -d --force-recreate --wait --wait-timeout 120
```

Для обновления измените `IMAGE_TAG` в `.env`, затем:

```bash
docker compose pull
docker compose up -d --force-recreate --wait --wait-timeout 120
```

Secret, сертификат и сайт находятся в именованном Docker-томе `state` проекта
`tg-ws-proxy` (обычно `tg-ws-proxy_state`). Обычные `down`, пересоздание и
обновление контейнеров сохраняют том. `docker compose down -v` удалит его,
включая secret и приватный ключ. Сохраняйте том при переносе на другой VPS.
Не меняйте имя Compose-проекта, иначе Docker выберет другой том.

Логи и остановка:

```bash
docker compose logs --tail 100 cover nfqws proxy
docker compose down
```

Логи прокси и команда `link` содержат приватную ссылку доступа.
При переходе с Python-установщика сначала остановите старый проект и задайте
его прежний secret в `access.secret`, чтобы сохранить ссылки. Готовый Compose
не импортирует старый `state.json` автоматически.
