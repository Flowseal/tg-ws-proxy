# Cloudflare Worker

An alternative (completely free, no domain purchase required unlike [CfProxy](./CfProxy.md)) method for proxying.

The proxy restores access to content that previously wouldn't load (reactions, certain stickers). If you are using a non-Premium account with this method and photos/videos still fail to load, leave only `4:149.154.167.220` in the `DC → IP` block.

##

1. **Add the following domains to [zapret](https://github.com/Flowseal/zapret-discord-youtube/) or any other DPI bypass software:**

```
cloudflare.com
cloudflare.dev
workers.dev
```

2. Create an account on [Cloudflare](https://dash.cloudflare.com/) (or log into an existing one)
	* **After creating your account, verify your email using the link sent to your inbox**
3. Select `Compute` → `Workers & Pages` from the left panel
   <img width="250" height="768" alt="image" src="https://github.com/user-attachments/assets/d81e3522-045a-4e65-9c2e-5545b7ad409a" />

4. Click the **`Create application`** button in the top right → `Start with Hello World!` → `Deploy`
   <img width="1406" height="193" alt="image" src="https://github.com/user-attachments/assets/7ac65944-8761-42a6-ab6d-ba5f9080c883" />
   <img width="586" height="379" alt="image" src="https://github.com/user-attachments/assets/ff901439-c2a1-4867-95de-e11b82a37044" />
   <img width="624" height="694" alt="image" src="https://github.com/user-attachments/assets/bb68d49a-166d-42a0-8fe2-bd2b16c0d066" />

5. Click the **`Edit code`** button in the top right, then replace the code on the left with the one [found at the bottom of this page](#worker-code)
    * If the code section fails to load, it means you missed the first step
    <img width="911" height="117" alt="image" src="https://github.com/user-attachments/assets/6bcdf839-d776-47e9-9d18-ba0efdf53244" />
	<img width="1027" height="512" alt="image" src="https://github.com/user-attachments/assets/daf131ed-82d5-40f0-a7eb-daeb598bea40" />


6. Click the **`Deploy`** button in the top right
   <img width="415" height="138" alt="image" src="https://github.com/user-attachments/assets/58d8f83e-d8b5-40cf-a30f-741d7311047b" />

7. Copy the domain from the field on the right and specify it in your **Cloudflare Worker** settings (or via the `--cfproxy-worker-domain` argument)
    * Example domain: `random-symbols-1234.username.workers.dev`
   <img width="414" height="182" alt="image" src="https://github.com/user-attachments/assets/4fb0b111-8026-4d17-b993-6c70ec37f1f5" />



### Worker Code

```javascript
import { connect } from "cloudflare:sockets";

function toBytes(data) {
	if (data instanceof ArrayBuffer) {
		return new Uint8Array(data);
	}
	if (typeof data === "string") {
		return new TextEncoder().encode(data);
	}
	if (data && typeof data.arrayBuffer === "function") {
		return data.arrayBuffer().then((ab) => new Uint8Array(ab));
	}
	return new Uint8Array();
}

export default {
	async fetch(request) {
		if ((request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
			return new Response("Expected websocket", { status: 426 });
		}

		const url = new URL(request.url);
		if (url.pathname !== "/apiws") {
			return new Response("Not found", { status: 404 });
		}

		const dst = url.searchParams.get("dst");
		const pair = new WebSocketPair();
		const client = pair[0];
		const server = pair[1];
		server.accept();

		const socket = connect({ hostname: dst, port: 443 });
		const tcpReader = socket.readable.getReader();
		const tcpWriter = socket.writable.getWriter();

		server.addEventListener("message", async (event) => {
			try {
				await tcpWriter.write(await toBytes(event.data));
			} catch {
				try {
					server.close(1011, "tcp write failed");
				} catch {}
			}
		});

		server.addEventListener("close", async () => {
			try {
				await tcpWriter.close();
			} catch {}
			try {
				socket.close();
			} catch {}
		});

		(async () => {
			try {
				while (true) {
					const { value, done } = await tcpReader.read();
					if (done) {
						break;
					}
					if (value) {
						server.send(value);
					}
				}
			} catch {
			} finally {
				try {
					server.close();
				} catch {}
				try {
					tcpReader.releaseLock();
				} catch {}
				try {
					socket.close();
				} catch {}
			}
		})();

		return new Response(null, { status: 101, webSocket: client });
	},
};
```
