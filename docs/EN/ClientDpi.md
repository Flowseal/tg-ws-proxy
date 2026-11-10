# Client ↔ VPS DPI resistance

Run the CLI on a VPS with Fake TLS enabled. DRS and first-byte ServerHello
splitting are enabled with Fake TLS; the installer enables TCPMSS automatically.
The installer also builds and enables zapret nfqws by default. The sizing and server-side split ideas
follow [mtproto.zig](https://github.com/sleep3r/mtproto.zig).

For prebuilt images on a VPS use [standalone Docker Compose](./DockerHub.md);
no host-side Python installer is needed. Source installation is described below.

## Automatic setup

Use the documented root [`config.toml.example`](../../config.toml.example):

```bash
cp config.toml.example config.toml
# Edit server.public_host and fake_tls.domain.
sudo python3 deploy/setup_masking.py --config config.toml
python3 deploy/setup_masking.py --config config.toml --mode docker
```

`server.public_host` is the VPS address in client links; `fake_tls.domain` is
Fake TLS SNI. `server.port` is the host listen port or Docker published port;
automatic Compose always listens on 1443 internally. `server.public_port`
changes only the port advertised in links, for external NAT.

Priority: **CLI > `TG_WS_PROXY_*` > TOML > defaults**. Select the file with
`--config` or `TG_WS_PROXY_CONFIG`; no file is loaded implicitly. Relative TOML
paths resolve against the config directory. Unknown keys, wrong types and
out-of-range values fail before system changes. TOML requires Python 3.11+ or
`tomli` on Python 3.8–3.10 (Debian/Ubuntu system Python:
`sudo apt-get install python3-tomli`). The old `--domain`
is a compatibility alias for `--fake-tls-domain`. CLI lists replace file lists;
positive flags such as `--fake-tls-drs`, `--fake-tls-split`, `--h2`, `--cfproxy`,
`--secure`, `--nfqws` can override disabled settings.

`[deployment]` applies only to the installer. Direct execution with
`python3 -m proxy.tg_ws_proxy --config config.toml` does not install nginx,
create certificates or change firewall rules. Set `access.secret` for direct
execution; the installer generates and preserves it in `state.json` when
omitted. `config.toml` is excluded from Git and the Docker build context.

`masking.mode = "local"` provisions nginx, a site and a certificate (default
port 8443). Provide `masking.certificate` and `masking.private_key` together for
a trusted certificate. `mode = "external"` creates no nginx or certificates:
probes go to `masking.backend_host` (default `fake_tls.domain`), default port
443, and receive the backend's certificate. Explicitly change `masking.port`
to 443 when switching the example to external mode. Remove certificate/key
settings. Never point the backend at the proxy's public listener itself.
The readiness check accepts external HTTP redirects/error responses and
verifies TLS/HTTP reachability, not DPI evasion. After editing TOML, rerun setup
with `--config`: services use generated settings. On the host, switching to
external mode removes only the installer-owned nginx configuration.


On Debian/Ubuntu with systemd, from the repository root:

```bash
sudo python3 deploy/setup_masking.py --fake-tls-domain proxy.example.com
```

This installs nginx, OpenSSL, venv support and headless proxy dependencies.
It creates a local HTTPS backend on `127.0.0.1:8443`, a cover page, a self-signed
EC certificate with the domain in its SAN, and a persistent 16-byte secret.
The proxy is installed as a DynamicUser systemd service on port 443 with only
CAP_NET_BIND_SERVICE. Its Python environment and runtime source snapshot are
under `/var/lib/tg-ws-proxy-masking`. Rerunning updates that source snapshot.
The script tests nginx config before loading it, preserves unrelated sites,
starts the services, verifies HTTPS through the public proxy port and prints
the private `ee` connection link.

Prefer your own domain resolving to the VPS. Public port 443 must be free;
an existing TLS-terminating server on that port must be reconfigured separately.
The backend port must also be available. Borrowed SNI can be detected through
IP/SNI inconsistency. A self-signed certificate completes TLS but remains
recognizable as self-signed; a trusted certificate is preferable:

```bash
sudo python3 deploy/setup_masking.py --fake-tls-domain proxy.example.com \
  --cert /etc/letsencrypt/live/proxy.example.com/fullchain.pem \
  --key /etc/letsencrypt/live/proxy.example.com/privkey.pem
```

Both PEM files are validated and copied into `tls/`; this command does not
issue an ACME certificate or renew an externally supplied certificate. Rerun
with both paths after renewal. Generated self-signed certificates are reused,
or regenerated on rerun when near expiry. Private keys, `state.json` and
`proxy.env` are private files. The certificate type is recorded in state.

Other options: `--port 2443`, `--mask-port 8444`, `--bind-address 0.0.0.0`,
`--secret <32 hex chars>`, `--state-dir <directory>`. Use `--public-host <VPS IP>`
if the connection address differs from the SNI domain; otherwise the printed
link uses that domain as its server. The PEM key must be unencrypted.
Repeat the same settings
when rerunning. The state directory holds the secret: keep it to retain links.
The existing `site/index.html` is preserved. Changing the domain requires
`--force-domain-change` and distributing new links. A stored supplied certificate
is never silently replaced with a self-signed one.

For a file-only preview (no packages installed or services started):

```bash
python3 deploy/setup_masking.py --fake-tls-domain proxy.example.com \
  --state-dir /tmp/tgws-preview --prepare-only
```

OpenSSL is required for preview certificate generation. This writes real
private keys and secrets, so protect the preview directory too. Host installs
require a state directory outside home and temporary directories because the
service uses ProtectHome/PrivateTmp. The default is suitable.

Inspect/stop the host proxy with `systemctl status tg-ws-proxy` and
`sudo systemctl disable --now tg-ws-proxy`. To remove the cover backend,
delete only `/etc/nginx/conf.d/tg-ws-proxy-masking.conf`, run `sudo nginx -t`
and reload nginx. Do not remove the state directory unless you intend to lose
the saved secret and site. To uninstall automatic TCPMSS as well, run
`sudo systemctl disable --now tg-ws-proxy-tcpmss`. Also stop
`tg-ws-proxy-dpi.service` and `tg-ws-proxy-nfqws.service` for nfqws (see below).

## Docker automatic setup

With Python 3.8+, OpenSSL, Docker Engine and Docker Compose v2 available:

```bash
python3 deploy/setup_masking.py --mode docker --fake-tls-domain proxy.example.com
```

The script creates `.tgws-masking/{state.json,proxy.env,nginx.conf,compose.yaml}`,
`tls/` and `site/`, builds the proxy image and starts a separate nginx container.
Only the proxy port is published; nginx is reachable over an internal Compose
network. No TLS private key is mounted into the proxy container. The proxy
waits for the cover healthcheck, and its own healthcheck exercises real HTTPS
through the masking path. The installer waits for health and performs an
additional SNI-specific HTTPS check. A failed check reports failure rather
than claiming successful protection.

The same certificate/secret/port options apply. No systemd is required
inside containers. With automatic TCPMSS, the proxy entrypoint briefly runs
as root with NET_ADMIN to configure its own network namespace, then clears
supplementary groups and drops to the image's `app` UID/GID before starting
the listener on 1443. `no-new-privileges` is enabled. Port 1443 is mapped to
public port 443 by default. The nginx master reads its private key
from a read-only mount. Keep the entire state directory between deployments.
It is excluded from Git and the Docker build context.

```bash
docker compose -f .tgws-masking/compose.yaml ps
docker compose -f .tgws-masking/compose.yaml logs proxy
docker compose -f .tgws-masking/compose.yaml down
```

`down` preserves generated files and secrets. Rerunning the installer rebuilds
and recreates this Compose project. For local testing, use
`--bind-address 127.0.0.1 --port 19443`. TCPMSS applies inside the proxy container,
before Docker DNAT/SNAT; no host firewall changes or privileged container are
needed. Rootless Docker or a kernel without TCPMSS support may require
`--no-tcpmss`. Docker Desktop or userland TCP forwarding may terminate the
external connection outside this namespace: verify the client-facing MSS on
your Linux VPS. Automatic nfqws runs in a separate container sharing the
proxy network namespace, with NFQUEUE before Docker NAT. Only the processor
retains NET_ADMIN/NET_RAW; the proxy listener runs as `app`. For rootless
Docker also pass `--no-nfqws`. See
[Docker environment options](./README.docker.md).

## Manual configuration

For an existing HTTPS backend, start from the repository root:

```bash
python3 proxy/tg_ws_proxy.py \
  --host 0.0.0.0 --port 443 --secret YOUR_32_HEX_SECRET \
  --fake-tls-domain proxy.example.com \
  --fake-tls-mask-host 127.0.0.1 --fake-tls-mask-port 8443
```

Binding 443 requires permission, e.g. CAP_NET_BIND_SERVICE in the proxy's
systemd service. Use the generated `ee` link with the public server address.
Changing the SNI domain requires distributing new links. The HTTPS backend
must be a separate endpoint, not the proxy's public port. Without an explicit
backend the SNI domain:443 is used, which would loop for a domain pointing
at this proxy. TLS probes retain their original SNI when relayed to the backend.

- DRS limits the first eight downstream records to 1369 bytes, then uses
  16384. State is per connection; no arbitrary padding is added to MTProto.
- The three-record ServerHello response is sent as one byte, then the rest
  after a 5–15 ms gap, following the existing 8–24 ms initial delay. TCP write
  boundaries are not guaranteed packet boundaries.
- Wrong-secret, wrong-SNI, replayed and non-TLS probes go to the real HTTPS
  backend, with a 30-second idle timeout, without a proxy-specific redirect.
- The in-memory replay cache keys exclude the timestamp bytes an observer can
  modify. It holds up to 8192 entries for 241 seconds, including the timestamp
  tolerance boundaries. Saturation never evicts live entries; new handshakes go
  to the cover instead. Listeners share the cache within a process; exiting the
  process clears it. No SQLite, files or Docker volume are used for anti-replay,
  and no setup is required on Linux or Docker. Statistics expose `tls_replay`
  and `tls_replay_full`. A replay can indicate active probing; it does not
  reliably identify a particular Revisor scanner.
- Startup profiling measures the backend's first encrypted record using the
  public SNI; it does not verify certificate trust or PQ compatibility.

Opt out with `--no-fake-tls-drs` / `--no-fake-tls-split`. Configure the gap with
`--fake-tls-split-delay-ms 5 --fake-tls-split-jitter-ms 10` (each 0..1000).
These are VPS CLI options; the tray editor does not expose them. Existing
local, non-FakeTLS connections are unaffected. See also
[nginx stream deployment](./FakeTlsNginx.md); use PROXY protocol only on a
private backend port reached by a trusted frontend.

## Linux network setup

The automatic installer enables MSS 88 by default, independently of nfqws:

```bash
sudo python3 deploy/setup_masking.py --fake-tls-domain proxy.example.com --tcpmss 92
sudo python3 deploy/setup_masking.py --fake-tls-domain proxy.example.com --no-tcpmss
```

The host setup installs iptables, copies the helper to the state directory,
and enables `tg-ws-proxy-tcpmss.service` before the proxy listener. The oneshot
reapplies rules on boot and removes them on stop. A second read-back checks
the actual rule after startup. Reruns stop the old unit, clean the old port
and replace its rules; `--no-tcpmss` stops/disables/removes the unit and its
owned rules. A mandatory IPv4 failure fails setup; IPv6 is attempted with
explicit warnings if unavailable. This does not prove bypass effectiveness.

`--prepare-only` only renders the service. In Docker the same MSS is applied
to internal port 1443 on each container start; disabling recreates the
container without NET_ADMIN, root bootstrap or its old network namespace.
The plain Docker image retains its unprivileged default unless TCPMSS is
explicitly enabled.

Preview, apply, inspect and remove the separate `TGWS_MSS_<port>` chain manually:

```bash
python3 deploy/linux/dpi.py apply --tcpmss-only --port 443 --mss 88 --dry-run
sudo python3 deploy/linux/dpi.py apply --tcpmss-only --port 443 --mss 88
sudo python3 deploy/linux/dpi.py status --tcpmss-only --port 443 --mss 88
sudo python3 deploy/linux/dpi.py remove --tcpmss-only --port 443
```

The MSS in the server's SYN-ACK influences client → VPS segmentation.
88 is a starting point, not a universally optimal value; it costs bandwidth
and can itself be recognizable. Add `--ipv6` to require IPv6 or
`--ipv6-optional` to attempt it while retaining working IPv4 rules.
The helper excludes loopback and does not flush unrelated firewall chains.
It checks installed rules, avoids duplicate jumps, and attempts to remove
its own rules if apply fails. Remove old-port rules before changing ports.

The installer builds zapret **v72.12** and enables nfqws automatically in both modes:

```bash
sudo python3 deploy/setup_masking.py --fake-tls-domain proxy.example.com
python3 deploy/setup_masking.py --mode docker --fake-tls-domain proxy.example.com
```

Defaults: `fake,split2`, TTL 6, split-pos 1, md5sig,badseq, any-protocol, NFQUEUE 200.
`badseq` gives fake packets an invalid TCP sequence number so short paths,
where the fake TTL has not expired yet, also discard them.
Use `--nfqws-ttl 8` to tune TTL or `--no-nfqws` to disable it and remove the
previous automatic rules/services. TCPMSS remains independently configurable.
`--prepare-only` only writes deployment files.

On the host, apt installs build dependencies. The staged build is atomically
installed at `/opt/tg-ws-proxy-nfqws/nfqws`; a build failure retains the installed
binary. An existing `/opt/zapret` is untouched. The installer creates
`tg-ws-proxy-nfqws.service` and `tg-ws-proxy-dpi.service`, restoring rules at boot.
It checks the queue handler, process status and IPv4 rules; IPv6 failures are
reported explicitly. Rerun setup after changing the public port.

In Docker the compiler stays in a build stage. A separate `nfqws` container
owns the network namespace and published port. The proxy joins with
`network_mode: service:nfqws` after the queue handler healthcheck succeeds.
No systemd, privileged mode or host firewall changes are needed. Inspect with:

```bash
docker compose -f .tgws-masking/compose.yaml logs nfqws
docker compose -f .tgws-masking/compose.yaml ps
```

The manual `deploy/linux/tg-ws-proxy-*.service` templates still use an external
`/opt/zapret/nfq/nfqws` and require path/port adjustments. Do not overwrite the
automatically generated services with these templates.

NFQUEUE targets downstream server data, excluding SYN/FIN/RST. The strategy
uses `fake,split2`, TTL and `md5sig,badseq`, with `any-protocol` because these packets
are not ClientHello. Inspect nfqws service status and logs as well as rule
status. `--queue-bypass` allows traffic when no queue consumer is present.
Packet processing adds CPU overhead. The Python proxy needs no firewall
capabilities. Use the public nginx port if nginx accepts the TCP connection.
Docker bridge/DNAT requires a different ruleset; these OUTPUT rules target
host networking or a regular VPS installation.

```bash
sudo systemctl disable --now tg-ws-proxy-dpi.service
sudo systemctl disable --now tg-ws-proxy-nfqws.service
sudo python3 deploy/linux/dpi.py remove --port 443
```

Use `--ipv6` on removal if used on apply. No firewall changes are made just
by updating the application. This reduces fingerprints, not guarantees
undetectability: IP, SNI, timing and traffic sizes remain observable. Validate
with real Telegram clients and packet captures in the target network; complete
IP blocking cannot be bypassed by camouflage alone.
