# Fake TLS + Upstream in Nginx

The domain in the `--fake-tls-domain` parameter should point to the same IP where the proxy is running.

## Example `nginx.conf` for Stream Module

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

## Running Proxy Behind Nginx

```bash
python3 proxy/tg_ws_proxy.py \
  --port 8446 \
  --host 127.0.0.1 \
  --fake-tls-domain example.com \
  --proxy-protocol \
  --secret <32-hex-chars>
```

The connection link will be in `ee`-secret format:

```text
tg://proxy?server=your.domain.com&port=443&secret=ee<secret><domain_hex>
```
