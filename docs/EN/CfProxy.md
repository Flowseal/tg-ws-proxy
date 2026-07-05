# Cloudflare Proxy

An alternative, free connection method is proxying through Cloudflare, which can be used for unreachable data centers. **All you need to get it working is a domain**. The application includes a default domain, but it can (and ideally should) be replaced with your own.
  
The proxy restores access to content that previously wouldn't load (reactions, certain stickers). If you are using a non-Premium account and photos/videos still fail to load, leave only `4:149.154.167.220` in the `DC → IP` block. If the CF proxy works, media will start loading again.

## Why should I set up my own domain?

Cloudflare limits the number of simultaneous WebSocket (WS) connections. The default domain could stop working at any moment.

## Setting up your own domain

1. Add your domain to Cloudflare (either by purchasing it directly from Cloudflare or by changing the NS servers: https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/). Domains cost around $1.50–$2.00 per year, and any domain extension will work.

2. In `SSL/TLS` → `Overview`, set the mode to **Flexible**.

3. In `DNS` → `Records`, add the following `A` records via `+ Add Record`:
- Name=`kws1`   IPv4=`149.154.175.50`
- Name=`kws2`   IPv4=`149.154.167.51`
- Name=`kws3`   IPv4=`149.154.175.100`
- Name=`kws4`   IPv4=`149.154.167.91`
- Name=`kws5`   IPv4=`149.154.171.5`
- Name=`kws203` IPv4=`91.105.192.100`

4. **Add your domain to [zapret](https://github.com/Flowseal/zapret-discord-youtube/) or any other DPI bypass software, as the Cloudflare subnet may be blocked (e.g., in Russia).**

5. In the `TgWsProxy` settings, replace the default domain with your own.

## Credits / Acknowledgments

- Original Idea: https://github.com/Nekogram/WSProxy
- Special thanks to [@UjuiUjuMandan](https://github.com/UjuiUjuMandan) for providing the information.
