# Telegram Test Environment (Test DCs)

Traffic routing to Telegram test data centers (test environment).
Useful for developing/testing bots and clients within the Telegram test environment.

## How to Enable

**Automatically.** Telegram Desktop marks test DCs with a +10000
offset (10001–10003). The proxy automatically detects this offset — no configuration needed, allowing
you to use production and test accounts simultaneously in a single client.

**Forced.** For clients that report test DCs as standard 1-3
(Telethon, TDLib) — they cannot be detected automatically. In this case, all traffic
is forcibly routed to test DCs (production accounts will stop working through this proxy).
To force this behavior, use the `--force-test-dc` flag in CLI:

```bash
tg-ws-proxy --force-test-dc      # + your --secret / --port
```

## Limitations

Only works for **direct DC → IP** and **Cloudflare Worker** routes (see [Setting up a Cloudflare Worker](./CfWorker.md)).