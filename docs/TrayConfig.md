# Файлы конфигурации Tray-приложения

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
  "cfproxy_user_domain": "",
  "cfproxy_worker_domain": "",
  "force_test_dc": false,
  "bot_api": false,
  "appearance": "auto"
}
```

Ключ `check_updates`: при `true` выполняется запрос к GitHub и сравнение текущей версии с последним релизом (только уведомление и ссылка на страницу загрузки).  
На Windows в конфиге может быть `autostart` (автозапуск при входе в систему).

Ключ `bot_api` (по умолчанию `false`): опциональный туннель для ботов через `api.telegram.org`. Обычным пользователям Desktop не нужен. См. [BotApi.md](./BotApi.md).
