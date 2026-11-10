# Client ↔ VPS DPI resistance

Run the CLI on a VPS with Fake TLS enabled. DRS and first-byte ServerHello
splitting are enabled with Fake TLS; Linux TCPMSS and external zapret nfqws
are optional deployment components. The sizing and server-side split ideas
follow [mtproto.zig](https://github.com/sleep3r/mtproto.zig).

Use a domain resolving to the VPS and a real HTTPS backend with a valid
certificate for that domain, for example nginx listening on 127.0.0.1:8443.
Prepare the certificate and website separately. A borrowed SNI can be
identified through IP/SNI inconsistency. Start from the repository root:

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
- Replay keys exclude the timestamp bytes an observer can modify. The cache
  survives listener restarts within the process, not process restarts.
- Startup profiling measures the backend's first encrypted record using the
  public SNI; it does not verify certificate trust or PQ compatibility.

Opt out with `--no-fake-tls-drs` / `--no-fake-tls-split`. Configure the gap with
`--fake-tls-split-delay-ms 5 --fake-tls-split-jitter-ms 10` (each 0..1000).
These are VPS CLI options; the tray editor does not expose them. Existing
local, non-FakeTLS connections are unaffected. See also
[nginx stream deployment](./FakeTlsNginx.md); use PROXY protocol only on a
private backend port reached by a trusted frontend.

## Linux network setup

Preview, apply, inspect and remove this proxy's dedicated mangle chain:

```bash
python3 deploy/linux/dpi.py apply --port 443 --mss 88 --dry-run
sudo python3 deploy/linux/dpi.py apply --port 443 --mss 88
sudo python3 deploy/linux/dpi.py status --port 443 --mss 88
sudo python3 deploy/linux/dpi.py remove --port 443
```

The MSS in the server's SYN-ACK influences client → VPS segmentation.
88 is a starting point, not a universally optimal value; it costs bandwidth
and can itself be recognizable. Add `--ipv6` to all operations for IPv6.
The helper excludes loopback and does not flush unrelated firewall chains.
It checks installed rules, avoids duplicate jumps, and attempts to remove
its own rules if apply fails. Remove old-port rules before changing ports.

For optional nfqws integration, install a compatible
[zapret nfqws](https://github.com/bol-van/zapret) separately. The helper does
not download binaries. Edit the templates in `deploy/linux` for your paths,
public port, IPv6 requirements, queue number and network-specific TTL:

```bash
sudo cp deploy/linux/tg-ws-proxy-{dpi,nfqws}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tg-ws-proxy-dpi.service
sudo python3 deploy/linux/dpi.py status --port 443 --mss 88 --nfqueue
```

Defaults: repository `/opt/tg-ws-proxy`, nfqws `/opt/zapret/nfq/nfqws`, queue
200 (must be unused), TTL 6, public port 443. Verify flags against your
nfqws version's `--help`. Add `--ipv6` to both DPI service commands if needed.
The DPI service starts the nfqws dependency. For TCPMSS alone, remove the
nfqws dependency/ordering and `--nfqueue` from that service. `--mss 0` permits
NFQUEUE without TCPMSS. If changing queue numbers, update both services.

NFQUEUE targets downstream server data, excluding SYN/FIN/RST. The strategy
uses `fake,split2`, TTL and `md5sig`, with `any-protocol` because these packets
are not ClientHello. Inspect nfqws service status and logs as well as rule
status. `--queue-bypass` allows traffic when no queue consumer is present.
Packet processing adds CPU overhead. The Python proxy needs no firewall
capabilities. Use the public nginx port if nginx accepts the TCP connection.
Docker bridge/DNAT requires a different ruleset; these OUTPUT rules target
host networking or a regular VPS installation.

```bash
sudo systemctl disable --now tg-ws-proxy-dpi.service
sudo systemctl stop tg-ws-proxy-nfqws.service
sudo python3 deploy/linux/dpi.py remove --port 443
```

Use `--ipv6` on removal if used on apply. No firewall changes are made just
by updating the application. This reduces fingerprints, not guarantees
undetectability: IP, SNI, timing and traffic sizes remain observable. Validate
with real Telegram clients and packet captures in the target network; complete
IP blocking cannot be bypassed by camouflage alone.
