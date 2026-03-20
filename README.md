> [!CAUTION]
>
> ### Реакция антивирусов
> Windows Defender часто ошибочно помечает приложение как **Wacatac**.  
> Если вы не можете скачать из-за блокировки, то:
> 1) Попробуйте скачать версию win7 (она ничем не отличается в плане функционала)
> 2) Отключите антивирус на время скачивания, добавьте файл в исключения и включите обратно  
>
> **Всегда проверяйте, что скачиваете из интернета, тем более из непроверенных источников. Всегда лучше смотреть на детекты широко известных антивирусов на VirusTotal**

# TG WS Proxy

Локальный SOCKS5-прокси для Telegram Desktop, который перенаправляет трафик через WebSocket-соединения к указанным серверам, помогая частично ускорить работу Telegram.  
  
**Ожидаемый результат аналогичен прокидыванию hosts для Web Telegram**: ускорение загрузки и скачивания файлов, загрузки сообщений и части медиа.

<img width="529" height="487" alt="image" src="https://github.com/user-attachments/assets/6a4cf683-0df8-43af-86c1-0e8f08682b62" />

## Как это работает

```
Telegram Desktop → SOCKS5 (127.0.0.1:1080) → TG WS Proxy → WSS (kws*.web.telegram.org) → Telegram DC
```

1. Приложение поднимает локальный SOCKS5-прокси на `127.0.0.1:1080`
2. Перехватывает подключения к IP-адресам Telegram
3. Извлекает DC ID из MTProto obfuscation init-пакета
4. Устанавливает WebSocket (TLS) соединение к соответствующему DC через домены `kws{N}.web.telegram.org`
5. Если WS недоступен (302 redirect) — автоматически переключается на прямое TCP-соединение

## 🚀 Быстрый старт

### Windows
Перейдите на [страницу релизов](https://github.com/Flowseal/tg-ws-proxy/releases) и скачайте **`TgWsProxy.exe`**. Он собирается автоматически через [Github Actions](https://github.com/Flowseal/tg-ws-proxy/actions) из открытого исходного кода.

При первом запуске откроется окно с инструкцией по подключению Telegram Desktop. Приложение сворачивается в системный трей.

**Меню трея:**
- **Открыть в Telegram** — автоматически настроить прокси через `tg://socks` ссылку
- **Перезапустить прокси** — перезапуск без выхода из приложения
- **Настройки...** — GUI-редактор конфигурации
- **Открыть логи** — открыть файл логов
- **Выход** — остановить прокси и закрыть приложение

## Установка из исходников

```bash
pip install -r requirements.txt
```

### Windows (Tray-приложение)

```bash
python windows.py
```

### Консольный режим

```bash
python proxy/tg_ws_proxy.py [--port PORT] [--dc-ip DC:IP ...] [-v]
```

**Аргументы:**

| Аргумент                            | По умолчанию                             | Описание                                       |
|-------------------------------------|------------------------------------------|------------------------------------------------|
| `--host`                            | `127.0.0.1`                              | IP-адрес SOCKS5-прокси                         |
| `--port`                            | `1080`                                   | Порт SOCKS5-прокси                             |
| `-u`, `-user`<br/>`-P`,`--password` | выкл.                                    | Логин и Пароль для авторизации в SOCKS5-прокси |
| `--dc-ip`                           | `2:149.154.167.220`, `4:149.154.167.220` | Целевой IP для DC (можно указать несколько раз)|
| `-v`, `--verbose`                   | выкл.                                    | Подробное логирование (DEBUG)                  |

**Примеры:**

```bash
# Стандартный запуск
python proxy/tg_ws_proxy.py

# Другой порт и дополнительные DC
python proxy/tg_ws_proxy.py --port 9050 --dc-ip 1:149.154.175.205 --dc-ip 2:149.154.167.220

# С подробным логированием
python proxy/tg_ws_proxy.py -v
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

## Конфигурация

Tray-приложение хранит данные в `%APPDATA%/TgWsProxy`:

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

Проект содержит спецификацию PyInstaller ([`windows.spec`](packaging/windows.spec)) и GitHub Actions workflow ([`.github/workflows/build.yml`](.github/workflows/build.yml)) для автоматической сборки.

```bash
pip install pyinstaller
pyinstaller packaging/windows.spec
```

## Лицензия

[MIT License](LICENSE)

## FORK

### 1) Linux/NAS
Сборка запуск
```bash
sudo apt update && sudo apt install git
git clone https://github.com/borisovmsw/tg-ws-proxy.git
cd tg-ws-proxy
docker build -t tg-proxy .
docker run -d --name tg-proxy -p 1080:1080 tg-proxy:latest -u userx -P 123456
```
### Подключаемся и проверяем
#### Локально
```
tg://socks/?server=127.0.0.1&port=1080&user=userx&pass=123456
```
#### Удаленно
```
tg://socks/?server=192.168.1.139&port=1080&user=userx&pass=123456
```

### 2) Openwrt ARM64

#### Настраиваем компьютер на компиляцию под ARM64 процессор роутера 
```bash
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name mybuilder --use
docker buildx inspect --bootstrap
```
#### Собираем образ для роутера
```bash
docker buildx build --platform linux/arm64 -t tg-proxy-flint:latest --load .
```
#### Сохраняем образ в файл
```bash
docker save tg-proxy-flint:latest -o tg-proxy-flint.tar
```
#### Закидываем образ на роутер
```bash
scp -O tg-proxy-flint.tar root@192.168.1.1:/tmp/
```

#### Подключаемся к роутеру
```bash
ssh root@192.168.1.1
```
#### Загружаем образ и удалям его
```bash
docker load -i /tmp/tg-proxy-flint.tar && rm /tmp/tg-proxy-flint.tar
```
#### Настройки роутера
Заходим в настройки Firewall - Traffic Rules
Создаем Rule с именем Docker
* Source zone - docker
* Destination zone - WAN
* на закладке Advanced Settings оставляем только ip4

#### Далее самое главное!!! Включаем WAN для Docker, информации крайне мало про эту срочку, времени на ее поиск ушло не мало
```bash
sed -i "s/^\([[:space:]]*\)list blocked_interfaces 'wan'/#\1&/" /etc/config/dockerd
```
#### Перезагружаем роутер или же выполняем команду
```bash
/etc/init.d/dockerd restart
```
#### Запускаем контейнер только на внутреннем IP
```bash
docker run -d --name tg-proxy -p 192.168.1.1:1080:1080 tg-proxy-flint:latest -u userx -P 123456
```
#### Смотрим логи, практически все INFO на каждое соединение заменил на DEBUG, чтобы не тратить ресурс постоянной памяти
```bash
docker logs tg-proxy
```
#### Тестируем
```bash
tg://socks/?server=192.168.1.1&port=1080&user=userx&pass=123456
```
