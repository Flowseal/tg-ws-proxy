# tg-ws-proxy — сборка и запуск через Docker.
#
# Конфигурация хранится в .env (шаблон — .env.example).
# Секрет прокси лежит в .secret и не меняется между перезапусками и
# перезагрузками, поэтому ссылка tg://proxy остаётся той же.
#
# Список команд: make help

-include .env

IMAGE     ?= tg-ws-proxy
CONTAINER ?= tg-ws-proxy
SERVICE   ?= tg-ws-proxy

TG_WS_PROXY_HOST      ?= 0.0.0.0
TG_WS_PROXY_PORT      ?= 1443
TG_WS_PROXY_NETWORK   ?= bridge
TG_WS_PROXY_DNS       ?=
TG_WS_PROXY_SECRET    ?=
TG_WS_PROXY_CF_WORKER ?=
TG_WS_PROXY_DC_IPS    ?=
EXTRA_ARGS            ?=

# --- Сеть ---
ifeq ($(TG_WS_PROXY_NETWORK),host)
NET_ARGS := --network host
else
NET_ARGS := -p $(TG_WS_PROXY_PORT):$(TG_WS_PROXY_PORT)
endif

ifneq ($(strip $(TG_WS_PROXY_DNS)),)
DNS_ARGS := --dns $(TG_WS_PROXY_DNS)
endif

# --- Прямые DC ---
# none  -> без прямых DC (только CF Worker);
# пусто -> дефолт образа; иначе -> явный список.
ifeq ($(strip $(TG_WS_PROXY_DC_IPS)),none)
DC_ENV :=
DC_CMD := --dc-ip
else ifeq ($(strip $(TG_WS_PROXY_DC_IPS)),)
DC_ENV :=
DC_CMD :=
else
DC_ENV := -e TG_WS_PROXY_DC_IPS=$(TG_WS_PROXY_DC_IPS)
DC_CMD :=
endif

.DEFAULT_GOAL := help
.PHONY: help image build rebuild run stop rm restart logs link secret shell env install uninstall

help:
	@printf '%s\n' \
		'Команды:' \
		'  make build      собрать образ $(IMAGE)' \
		'  make rebuild    собрать образ без кеша' \
		'  make run        запустить контейнер' \
		'  make stop       остановить контейнер' \
		'  make rm         удалить контейнер' \
		'  make restart    перезапустить контейнер' \
		'  make logs       показать логи' \
		'  make link       показать ссылку tg://proxy' \
		'  make secret     показать секрет' \
		'  make shell      открыть оболочку в контейнере' \
		'  make env        создать .env из .env.example' \
		'  make install    установить systemd-сервис (автозапуск)' \
		'  make uninstall  удалить systemd-сервис'

image:
	@docker image inspect $(IMAGE):latest >/dev/null 2>&1 || $(MAKE) build

build:
	docker build -t $(IMAGE) .

rebuild:
	docker build --no-cache -t $(IMAGE) .

run: image
	@SECRET="$(TG_WS_PROXY_SECRET)"; \
	if [ -z "$$SECRET" ]; then \
		if [ ! -f .secret ]; then \
			openssl rand -hex 16 > .secret; chmod 600 .secret; \
			printf '%s\n' 'Создан .secret — сохраните его: он задаёт секрет прокси.'; \
		fi; \
		SECRET="$$(cat .secret)"; \
	fi; \
	docker rm -f $(CONTAINER) >/dev/null 2>&1 || true; \
	docker run -d \
		--name $(CONTAINER) \
		--restart=always \
		$(NET_ARGS) $(DNS_ARGS) \
		-e TG_WS_PROXY_HOST="$(TG_WS_PROXY_HOST)" \
		-e TG_WS_PROXY_PORT="$(TG_WS_PROXY_PORT)" \
		-e TG_WS_PROXY_SECRET="$$SECRET" \
		$(if $(strip $(TG_WS_PROXY_CF_WORKER)),-e TG_WS_PROXY_CF_WORKER="$(TG_WS_PROXY_CF_WORKER)",) \
		$(DC_ENV) \
		$(IMAGE):latest \
		$(DC_CMD) $(EXTRA_ARGS) >/dev/null; \
	docker logs $(CONTAINER) 2>&1 | grep -o 'tg://[^ ]*' | head -1

stop:
	@docker stop $(CONTAINER) 2>/dev/null || printf '%s\n' 'Контейнер не запущен.'

rm:
	@docker rm -f $(CONTAINER) 2>/dev/null || printf '%s\n' 'Контейнер не найден.'

restart:
	@$(MAKE) rm
	@$(MAKE) run

logs:
	docker logs -f $(CONTAINER)

link:
	@docker logs $(CONTAINER) 2>&1 | grep -o 'tg://[^ ]*' | head -1 || printf '%s\n' 'Ссылка не найдена.'

secret:
	@if [ -n "$(TG_WS_PROXY_SECRET)" ]; then \
		printf '%s\n' '$(TG_WS_PROXY_SECRET)'; \
	elif [ -f .secret ]; then cat .secret; \
	else printf '%s\n' 'Секрет ещё не создан.'; fi

shell:
	docker exec -it $(CONTAINER) /bin/sh

env:
	@if [ -f .env ]; then \
		printf '%s\n' '.env уже существует.'; \
	else \
		cp .env.example .env; \
		printf '%s\n' 'Создан .env — отредактируйте его при необходимости.'; \
	fi

install:
	@sed -e 's|__WORKDIR__|$(CURDIR)|g' -e 's|__USER__|'"$$(id -un)"'|g' \
		tg-ws-proxy.service | sudo tee /etc/systemd/system/$(SERVICE).service >/dev/null
	@sudo systemctl daemon-reload
	@sudo systemctl enable $(SERVICE)
	@printf '%s\n' 'Сервис установлен. Запуск: sudo systemctl start $(SERVICE)'

uninstall:
	-@sudo systemctl disable --now $(SERVICE) 2>/dev/null || true
	-@sudo rm -f /etc/systemd/system/$(SERVICE).service
	@sudo systemctl daemon-reload
	@printf '%s\n' 'Сервис удалён.'
