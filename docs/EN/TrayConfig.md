# Tray Application Configuration Files

The tray application stores data in:

- **Windows:** `%APPDATA%/TgWsProxy`
- **macOS:** `~/Library/Application Support/TgWsProxy`
- **Linux:** `~/.config/TgWsProxy` (or `$XDG_CONFIG_HOME/TgWsProxy`)

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
  "language": "en",
  "config_version": 2
}
```

The `check_updates` key: when `true`, performs a request to GitHub and compares the current version with the latest release (notification and link to download page only).  
On Windows, the config may contain `autostart` (auto-start on system login).  
`config_version` is a service field used to migrate old configs, do not change it manually.
