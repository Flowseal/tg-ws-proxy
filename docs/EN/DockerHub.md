# Prebuilt images and standalone Docker Compose

The root `docker-compose.yaml` uses three prebuilt images and has no `build:` entries.
The VPS needs Docker Engine and Compose v2; no Python, Git or compiler is needed.

## Build and publish

From the repository root, choose your Docker Hub namespace and a shared tag:

```bash
export DOCKERHUB_NAMESPACE=your-dockerhub-login
export IMAGE_TAG=vps-1
docker login
docker buildx create --name tgws-builder --use
docker buildx inspect --bootstrap

docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/proxy.Dockerfile \
  -t "$DOCKERHUB_NAMESPACE/tg-ws-proxy:$IMAGE_TAG" --push .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/cover.Dockerfile \
  -t "$DOCKERHUB_NAMESPACE/tg-ws-proxy-cover:$IMAGE_TAG" --push .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/nfqws.Dockerfile \
  -t "$DOCKERHUB_NAMESPACE/tg-ws-proxy-nfqws:$IMAGE_TAG" --push .
```

See the [Buildx reference](https://docs.docker.com/reference/cli/docker/buildx/build/).
Create the builder only once. For local testing, choose one platform and use
`--load` instead of `--push`. Publication is performed explicitly by the owner.

## Deploy

Copy `docker-compose.yaml`, `config.toml.example` and `.env.example` to the VPS.
Rename the examples to `config.toml` and `.env`. Set the published namespace
and tag in `.env`, then edit `server.public_host` (your VPS address) and
`fake_tls.domain` (SNI) in TOML.

```dotenv
DOCKERHUB_NAMESPACE=your-dockerhub-login
IMAGE_TAG=vps-1
PUBLIC_PORT=443
PUBLIC_BIND_ADDRESS=0.0.0.0
```

Static Compose publishes ports using `.env`, not TOML: `server.port` and
`server.bind_address` do not control port publication in this deployment.
The listener always uses `0.0.0.0:1443` internally. Links use `PUBLIC_PORT`,
unless `server.public_port` explicitly overrides the advertised port for NAT.

Open the public TCP port in the VPS provider firewall, then run:

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 120
docker compose ps
docker compose exec -T proxy python /app/deploy/docker/entrypoint.py link
```

Use `sudo` for Docker commands if required. The source TOML may have mode 600:
only the cover bootstrap reads the host file. The unprivileged proxy receives
validated settings and its secret through a read-only state volume.

`cover` generates a site, self-signed certificate and persistent secret.
`nfqws` owns the network namespace, configures TCPMSS and NFQUEUE, and publishes
the port. `proxy` shares that namespace and runs as UID/GID 10001 without
capabilities. Services wait for dependency healthchecks and restart after a
Docker/host reboot. This deployment requires rootful Docker Engine on Linux.

## Configuration and persistent state

Leave `access.secret` unset to generate it once. A named `state` volume stores
the secret, certificate and site across recreation and upgrades. Preserve the
volume when moving servers. Changing the Compose project name selects another
volume. `down` preserves state; `down -v` deletes the secret and private key.

For a trusted certificate, uncomment the `./certs` mount for cover, place your
PEMs in that directory, and set `masking.certificate = "certs/fullchain.pem"`
and `masking.private_key = "certs/privkey.pem"`. ACME is not automated; recreate
containers after renewing the PEMs. The key is not mounted into proxy or nfqws.

With `masking.mode = "external"`, set port 443 and optionally `backend_host`,
and omit local certificate settings. Cover still initializes state but does
not run nginx. All three services remain present.

`deployment.tcpmss = 0` disables MSS clamping. `deployment.nfqws = false`
disables the queue handler, while its container retains the shared namespace
and can still configure TCPMSS. `deployment.nfqws_ttl` controls fake TTL.
`deployment.mode` and `deployment.state_dir` apply only to the legacy installer.

After any configuration change, recreate the complete project:

```bash
docker compose up -d --force-recreate --wait --wait-timeout 120
```

To upgrade, edit `IMAGE_TAG`, run `docker compose pull`, then recreate as above.
Inspect with `docker compose logs --tail 100 cover nfqws proxy`. Proxy logs and
the `link` command contain private access links.

When migrating from the Python installer, stop the old project and set its
existing secret in `access.secret`. The old state JSON is not imported.
