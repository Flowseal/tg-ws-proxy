<div align="center">

**🇷🇺 Русский • [🇬🇧 English](./EN/README.md)**

</div>

<div align="center">
	<br />
	<p>
		<img width="1729" height="910" alt="tgwsproxy" src="./images/workflow.png" />
	</p>
</div>

##

> [!TIP]
>
> ### [🎉 Поддержать меня](./RU/Funding.md)
>
> **USDT (TRC20)**: `TXPnKs2Ww1RD8JN6nChFUVmi5r2hqrWjuu`  
> **BTC**: `bc1qr8vd6jelkyyry3m4mq6z5txdx4pl856fu6ss0w`  
> **ETH**: `0x1417878fdc5047E670a77748B34819b9A49C72F1`  

> [!CAUTION]
>
> ### Реакция антивирусов
>
> Антивирусы часто ошибочно помечают приложение как вирус из-за упаковщика.  
> Если вы не можете скачать из-за блокировки антивирусом, то:
>
> 1) **Попробуйте скачать версию для Windows 7 (по функциональности она не отличается)**
> 2) Отключите антивирус на время скачивания, добавьте файл в исключения и включите обратно  
>
> Всегда проверяйте, что скачиваете из интернета, тем более из непроверенных источников. Всегда лучше смотреть на детекты широко известных антивирусов на VirusTotal

# TG WS Proxy

**Локальный MTProto-прокси** для Telegram Desktop, который **ускоряет работу Telegram**, перенаправляя трафик через WebSocket-соединения. Данные передаются в том же зашифрованном виде, а для работы не нужны сторонние серверы.

<picture>
  <source srcset="./images/preview-dark.png" media="(prefers-color-scheme: dark)">
  <img src="./images/preview-white.png">
</picture>

## Навигация

- **🚀 Быстрый старт**
  - **[Windows](./RU/README.windows.md)**
  - **[macOS](./RU/README.macos.md)**
  - **[Linux](./RU/README.linux.md)**
  - **[Docker](./RU/README.docker.md)**
- [Настройка Cloudflare Worker'а (бесплатный аналог CF-прокси)](./RU/CfWorker.md)
- [Настройка Cloudflare-домена (CF-прокси)](./RU/CfProxy.md)
- [Тестовое окружение Telegram (тестовые DC)](./RU/TestDc.md)
- [Fake TLS + upstream в Nginx](./RU/FakeTlsNginx.md)
- [Защита клиент ↔ VPS: Fake TLS, DRS, TCPMSS и nfqws](./RU/ClientDpi.md)
- [Файлы конфигурации Tray-приложения](./RU/TrayConfig.md)
- [Установка из исходников](./RU/BuildFromSource.md)
- [Руководство для контрибьюторов](./CONTRIBUTING.md)

## VPS: автоматическая настройка маскировки

Из корня скачанного репозитория, на Debian/Ubuntu с systemd:

```bash
sudo python3 deploy/setup_masking.py --domain proxy.example.com
```

Скрипт установит nginx и зависимости консольного прокси, создаст HTTPS-сайт,
самоподписанный сертификат, постоянный secret и systemd-сервис, затем проверит
HTTPS через прокси и напечатает `ee`-ссылку. Замените домен на выбранный вами;
предпочтительно использовать свой домен, указывающий на VPS. Порт 443 должен
быть свободен. Для готового доверенного сертификата доступны `--cert` и `--key`.

Для готовых образов из Docker Hub используйте [статический Compose](./RU/DockerHub.md):
на VPS не нужны Python и сборка. Ниже — альтернативная сборка из исходников.

В Docker, при установленном Docker Compose и OpenSSL:

```bash
python3 deploy/setup_masking.py --mode docker --domain proxy.example.com
```

Создаются и запускаются три контейнера: прокси, внутренний HTTPS backend и nfqws.
Повторный запуск сохраняет secret, сертификат и ваш сайт. Для подготовки
файлов без запуска добавьте `--prepare-only`.

TCPMSS=88 включается автоматически: на Linux — отдельным systemd-сервисом,
в Docker — внутри сетевого пространства контейнера перед запуском прокси
от `app`. Значение меняется через `--tcpmss 92`; `--no-tcpmss` отключает
защиту и удаляет ранее установленные автоматические правила.
nfqws также устанавливается и включается автоматически (TTL 6, NFQUEUE 200).
TTL меняется через `--nfqws-ttl 8`, отключение — `--no-nfqws`.
Самоподписанный сертификат не обеспечивает доверие браузера. Подробности, пути файлов и отключение:
[Защита клиент ↔ VPS](./RU/ClientDpi.md) и [Docker](./RU/README.docker.md).

## Windows: быстрый вход

Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте:

- `TgWsProxy_windows.exe` (Windows 10+ x64)
- `TgWsProxy_windows_arm64.exe` (Windows 10+ ARM64)
- `TgWsProxy_windows_7_64bit.exe` (Windows 7 x64)
- `TgWsProxy_windows_7_32bit.exe` (Windows 7 x32)

При первом запуске откроется окно с инструкцией по подключению Telegram Desktop. **Приложение сворачивается в системный трей.**

### Меню трея

- **Открыть в Telegram** — автоматически настроить прокси через ссылку `tg://proxy`
- **Скопировать ссылку** — скопировать ссылку для подключения
- **Перезапустить прокси** — перезапуск без выхода из приложения
- **Настройки...** — GUI-редактор конфигурации (версия приложения, опциональная проверка обновлений с GitHub)
- **Открыть логи** — открыть файл логов
- **Выход** — остановить прокси и закрыть приложение

### Настройка Telegram Desktop

**Автоматическая настройка**

Щелкните правой кнопкой мыши по значку в трее и выберите **«Открыть в Telegram»**.

Если не сработало (Telegram не открылся с подключением), выполните шаги ниже:

1. Щелкните правой кнопкой мыши по значку в трее и выберите **«Скопировать ссылку»**
2. Отправьте ссылку в «Избранное» в Telegram и нажмите по ней левой кнопкой мыши
3. Подключитесь

**Ручная настройка**

1. Telegram → **Настройки** → **Продвинутые настройки** → **Тип подключения** → **Прокси**
2. Добавьте прокси:
   - **Тип:** MTProto
   - **Сервер:** `127.0.0.1` (или переопределенный вами)
   - **Порт:** `1443` (или переопределенный вами)
   - **Secret:** из настроек или логов

## Как это работает

```
Telegram Desktop → MTProto Proxy (127.0.0.1:1443) → WebSocket → Telegram DC
```

1. Приложение поднимает MTProto прокси на `127.0.0.1:1443`
2. Перехватывает подключения к IP-адресам Telegram
3. Извлекает DC ID из MTProto obfuscation init-пакета
4. Устанавливает WebSocket-соединение (TLS) к соответствующему DC через домены Telegram
5. Если WS недоступен (302 redirect) — автоматически переключается на CfProxy / прямое TCP-соединение

> [!IMPORTANT] 
> ### Не грузит фото/видео?
> **Удалите в настройках прокси в DC → IP всё, кроме `4:149.154.167.220`**  
> **Если это не помогло, полностью очистите это поле**  
> Подобная проблема встречается на аккаунтах без Premium  
> Если это не помогло, настройте собственный домен по инструкции: [CfProxy.md](./RU/CfProxy.md)

## Автоматическая сборка

Проект содержит спецификации PyInstaller ([`packaging/windows.spec`](../packaging/windows.spec), [`packaging/macos.spec`](../packaging/macos.spec), [`packaging/linux.spec`](../packaging/linux.spec)) и GitHub Actions workflow ([`.github/workflows/build.yml`](../.github/workflows/build.yml)) для автоматической сборки.

Минимально поддерживаемые версии ОС для текущих бинарных сборок:

- Windows 10+ x64 для `TgWsProxy_windows.exe`
- Windows 10+ ARM64 для `TgWsProxy_windows_arm64.exe`
- Windows 7 (x64) для `TgWsProxy_windows_7_64bit.exe`
- Windows 7 (x32) для `TgWsProxy_windows_7_32bit.exe`
- Intel macOS 10.15+
- Apple Silicon macOS 11.0+
- Linux x86_64 (требуется AppIndicator для системного трея)

## Контрибьюторы

Спасибо всем, кто помогает развивать проект ❤️

<a href="https://github.com/Flowseal/tg-ws-proxy/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Flowseal/tg-ws-proxy" />
</a>

## Лицензия

[MIT License](../LICENSE)
