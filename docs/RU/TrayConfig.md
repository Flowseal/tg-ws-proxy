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
  "buf_kb": 0,
  "pool_size": 4,
  "log_max_mb": 5,
  "check_updates": true,
  "cfproxy": true,
  "h2": true,
  "cfproxy_user_domain_enabled": false,
  "cfproxy_user_domain": [],
  "cfproxy_worker_enabled": false,
  "cfproxy_worker_domain": [],
  "force_test_dc": false,
  "no_secure": false,
  "appearance": "auto",
  "language": "ru",
  "config_version": 2
}
```

Ключ `check_updates`: при `true` выполняется запрос к GitHub и сравнение текущей версии с последним релизом (только уведомление и ссылка на страницу загрузки).  
На Windows в конфиге может быть `autostart` (автозапуск при входе в систему).  
`config_version` — служебное поле для миграции старых конфигов, вручную его менять не нужно.
