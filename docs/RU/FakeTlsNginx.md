# Fake TLS + upstream в nginx

Домен в параметре `--fake-tls-domain` должен указывать на тот же IP, на котором запущен прокси.

## Пример `nginx.conf` для stream-модуля

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

## Запуск прокси за Nginx

```bash
python3 proxy/tg_ws_proxy.py \
  --port 8446 \
  --host 127.0.0.1 \
  --fake-tls-domain example.com \
  --proxy-protocol \
  --secret <32-hex-chars>
```

Ссылка для подключения будет в формате `ee`-секрета:

```text
tg://proxy?server=your.domain.com&port=443&secret=ee<secret><domain_hex>
```
