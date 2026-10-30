# TG WS Proxy для Android (форк)

Android-порт [Flowseal/tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy), который живёт в форке [chelpik1337/tg-ws-proxy](https://github.com/chelpik1337/tg-ws-proxy), ветка `android-port`. Pull request в оригинальный репозиторий не принят, поэтому сборка ведётся из форка.

> **Проект создан с помощью искусственного интеллекта (Claude, Anthropic).** Код написан ИИ-ассистентом под руководством автора, а не вручную.
>
> Приложение проверено на **Samsung Galaxy S10** с **LineageOS 23.0 (Android 16)** и root-доступом. На других устройствах и версиях Android оно не проверялось, поведение там может отличаться.

## Что умеет

- Запуск и остановка прокси как foreground-сервиса с уведомлением
- Генерация секрета и ссылка `tg://proxy` с кнопками «Скопировать» и «Открыть в Telegram»
- Настройки, как в десктопной версии: адрес, порт, секрет, DC:IP, буфер, пул, Cloudflare-прокси, свой домен, Cloudflare Worker, HTTP/2 для медиа, отключение защищённого режима, подробные логи
- Тема: системная, светлая или тёмная
- Язык: русский или английский
- Автозапуск при загрузке телефона
- Тест подключения: автоподбор домена Cloudflare, проверка всех доменов, проверка Cloudflare Worker, результаты по DC 1, 2, 3, 4, 5, 203
- Логи: просмотр последних записей, выгрузка файла через системное меню «Поделиться». Файл ротируется: 5 МБ, один бэкап
- Проверка обновлений через GitHub Releases с проверкой sha256

## Требования

Для сборки:

- **JDK 17** (проверено с JDK 17.0.12). JDK 21 и новее с AGP 8.7 могут не работать.
- **Android SDK** с платформой **Android 15 (API 35)** и build-tools для неё. Проще всего поставить через Android Studio (SDK Manager) или командную строку `sdkmanager`.
- Интернет при первой сборке: Gradle, Maven-зависимости и pip-пакеты Chaquopy скачиваются автоматически.
- Gradle не нужно ставить отдельно: используется wrapper (`gradlew`), версия 8.9.

Для запуска на телефоне:

- Android **7.0 (API 24)** и новее
- Процессор **arm64-v8a** (почти все современные телефоны), **armeabi-v7a** (старые 32-битные) или **x86_64** (эмуляторы)
- Разрешение на уведомления на Android 13+ (запрашивается при первом запуске)

Root-доступ не нужен: прокси слушает порт на `127.0.0.1` внутри приложения.

## Структура

```
android/
├── app/
│   ├── build.gradle.kts          # сборка, Chaquopy, копирование Python-кода
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── java/com/tgwsproxy/   # Kotlin: UI, сервис, настройки, обновления, логи
│       ├── python-sources/       # tg_android.py: мост между Kotlin и Python-прокси
│       ├── python-overrides/     # _aes.py: AES-CTR через javax.crypto вместо cryptography
│       └── res/                  # ресурсы, темы, file_paths.xml для FileProvider
├── build.gradle.kts
├── settings.gradle.kts
├── gradle.properties
└── gradlew, gradlew.bat
```

Ядро прокси лежит в корне репозитория, в папке `proxy/`. При сборке Gradle копирует её в `app/src/main/python` вместе с `python-sources` и `python-overrides`. Папка `app/src/main/python` генерируется и в git не входит.

## Сборка

### 1. Клонировать форк

```bash
git clone -b android-port https://github.com/chelpik1337/tg-ws-proxy.git
cd tg-ws-proxy/android
```

Сборка должна идти из папки `android/` внутри репозитория: иначе Gradle не найдёт `../../proxy`.

### 2. Указать путь к Android SDK

Создайте файл `android/local.properties` (он в `.gitignore`):

```properties
sdk.dir=C\:\\Users\\ВАШ_ПОЛЬЗОВАТЕЛЬ\\AppData\\Local\\Android\\Sdk
```

Для Linux и macOS:

```properties
sdk.dir=/home/user/Android/Sdk
```

Если открываете проект в Android Studio, файл создаётся сам.

### 3. Указать JDK 17

Windows PowerShell (только на текущую сессию):

```powershell
$env:JAVA_HOME = "C:\Program Files\Java\jdk-17"
```

Linux и macOS:

```bash
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
```

### 4. Собрать отладочный APK

Windows:

```powershell
.\gradlew.bat assembleDebug
```

Linux и macOS:

```bash
./gradlew assembleDebug
```

Первая сборка занимает несколько минут: Gradle скачивает зависимости, Chaquopy скачивает Python 3.11 и pip-пакеты.

Готовый файл:

```
app/build/outputs/apk/debug/app-debug.apk
```

### 5. Установить на телефон

Включите на телефоне «Для разработчиков» и «Отладку по USB». Затем:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Флаг `-r` обновляет приложение и сохраняет настройки, если подпись APK совпадает.

Или скопируйте APK на телефон и установите вручную. Для этого Android может попросить разрешить установку из неизвестных источников.

## Проверка работы

1. Откройте приложение и нажмите «Запустить». Появится уведомление «TG WS Proxy работает».
2. Нажмите «Открыть в Telegram» или скопируйте ссылку `tg://proxy?...` и откройте её в Telegram. Telegram предложит добавить прокси.
3. Откройте «Тест подключения» и нажмите «Автоподбор домена».
4. Если что-то не работает, откройте «Логи» и нажмите «Выгрузить», либо смотрите лог через adb:

```bash
adb logcat -s python.stdout python.stderr AndroidRuntime
```

## Сборка release APK

Для обновлений через приложение нужен APK, подписанный **постоянным** release-ключом. Debug-ключ Android генерирует автоматически, и он отличается на разных машинах. Обновление поверх APK с другой подписью невозможно: Android откажет в установке.

Создайте ключ один раз и храните его в надёжном месте, потеря ключа означает невозможность обновлять установленное приложение:

```bash
keytool -genkeypair -v -keystore tgwsproxy-release.jks -keyalg RSA -keysize 4096 -validity 10000 -alias tgwsproxy
```

Затем настройте подпись в `app/build.gradle.kts` (в репозиторий ключ и пароли не коммитить, читайте их из `local.properties` или переменных окружения) и соберите:

```bash
./gradlew assembleRelease
```

Этот шаг в проекте пока **не настроен**: `build.gradle.kts` собирает только debug-подпись.

## Обновления

Кнопка «Обновления» проверяет последний релиз `Flowseal/tg-ws-proxy` на GitHub. Приложение ищет в релизе файл с именем **`TgWsProxy_android.apk`** и сверяет sha256 с digest из GitHub API.

Пока что так:

- В релизе такого APK **нет**. Кнопка покажет ошибку «в релизе нет TgWsProxy_android.apk».
- Чтобы обновления работали, нужно публиковать release APK с этим именем и подписью постоянным ключом.
- Без публикации обновляйте вручную: `git pull`, сборка, `adb install -r`.

Обновление ядра прокси (`proxy/`) в приложение приходит только через пересборку APK. Сам APK не обновляется автоматически, пока не настроены публикация и подпись.

Если upstream меняет функции, которые использует `tg_android.py` (`proxy_config`, `_run`, `parse_dc_ip_list`, `coerce_domain_list`, `create_ssl_context`, `CFPROXY_DEFAULT_DOMAINS`), прокси может упасть на старте или в тестах. Тогда правится `tg_android.py`.

## Ограничения и известные проблемы

- Проверено на одном устройстве: Samsung Galaxy S10, LineageOS 23.0, Android 16, root. Другие модели и версии Android не проверены.
- Автозапуск на Android 14+ может не сработать из-за ограничений запуска foreground-сервиса из фона. Отдельно его проверьте на своём телефоне.
- Android 7–8 и старые 32-битные устройства не проверялись. Python-зависимости и TLS на них могут вести себя иначе.
- Тексты интерфейса продублированы в `S.kt` и не загружаются из `ui/i18n/*.json` десктопа. При изменении десктопных строк их нужно обновить вручную.
- Приложение не подписано для Google Play, и публикации там нет.
- Проект создан ИИ-ассистентом. Перед продакшен-использованием проверьте код и поведение.

## Лицензия

Проект распространяется под лицензией MIT, как и основной репозиторий [Flowseal/tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy). Текст лицензии: [LICENSE](../LICENSE).
