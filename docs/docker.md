# Docker

Run Cloudify in a container on any machine with Docker: Linux servers, NAS devices (Synology, QNAP, Unraid,
TrueNAS SCALE), Raspberry Pi, or Docker Desktop on macOS and Windows. The image contains Python, Cloudify,
cloudflared (for the image's CPU architecture), cron and the OpenSSH client. All Docker files are in
[`docker/`](../docker/README.md); run the commands below from the repository root.

## Quick start with Docker Compose (recommended)

```bash
git clone https://github.com/yash7158/Cloudify_v2.git
cd Cloudify_v2
docker compose -f docker/docker-compose.yml up -d --build
```

Open `http://localhost:8765` and create the first account with the setup code from the log:

```bash
docker logs cloudify | grep "Setup code"
```

Then **Connect Cloudflare**: the dashboard shows a link; open it in your browser and pick your domain. Continue
with [Getting started](getting-started.md#option-3-the-same-in-the-dashboard).

What the compose file sets up:

| Setting | Why |
| --- | --- |
| `restart: unless-stopped` | The container, and with it every tunnel with auto-start, comes back after a crash or a reboot of the server (unless you stopped it yourself). |
| volume `cloudify-data` → `/root/.cloudflared` | Cloudflare logins, tunnel credentials and configs, Cloudify users and connections survive re-creating or upgrading the container. |
| port `127.0.0.1:8765:8765` | The dashboard, reachable from this machine only (see *Securing the dashboard*). |
| network `cloudify` | Put your own containers on it and route to them by container name. |

Make sure Docker itself starts at boot: `sudo systemctl enable docker` (Docker Desktop: *Start Docker Desktop
when you sign in*).

## Without Compose

```bash
docker build -f docker/Dockerfile -t cloudify:latest .
docker run -d --name cloudify --restart unless-stopped \
  -p 127.0.0.1:8765:8765 -v cloudify-data:/root/.cloudflared cloudify:latest
```

`--restart` and `-v` are essential: a restart policy belongs to the container, not the image, and without the
volume everything is lost when the container is removed.

## Building the image

```bash
docker build -f docker/Dockerfile -t cloudify:latest .                                   # pinned cloudflared
docker build -f docker/Dockerfile --build-arg CLOUDFLARED_VERSION=latest -t cloudify:latest .   # newest cloudflared
docker buildx build -f docker/Dockerfile --platform linux/amd64,linux/arm64 -t cloudify:latest .  # multi-arch
```

The build context is the repository root; `docker/Dockerfile.dockerignore` keeps tests, Git data and local
files out of the image. Supported architectures: amd64, arm64 (Raspberry Pi 4/5 with a 64-bit OS, Apple
Silicon, AWS Graviton), arm (32-bit Raspberry Pi OS) and 386.

## Routing to your services

A route's *service host* is resolved **inside the container**, so `localhost` means the container itself.
Pick the right host for where your service runs:

| Service runs... | Service host in the route | Setup |
| --- | --- | --- |
| in another container | the container name, e.g. `frontend` | Both containers on the same network (see below) |
| on the Docker host (Linux) | `host.docker.internal` | Add `extra_hosts: ["host.docker.internal:host-gateway"]` to the `cloudify` service |
| on the Docker host (Docker Desktop) | `host.docker.internal` | Nothing, it exists already |
| on another machine in your LAN | its IP, e.g. `192.168.1.20` | Nothing |
| on the host, with host networking | `localhost` | `network_mode: host` (Linux only; remove `ports:` and `networks:`) |

Example: an app container next to Cloudify.

```yaml
# docker/docker-compose.yml (excerpt)
services:
  cloudify:
    # ... as shipped ...
  frontend:
    image: nginx:alpine
    restart: unless-stopped
    networks:
      - cloudify
```

Then add the route `app.example.com → frontend : 80` in the dashboard, or
`docker exec cloudify cloudify route add app.example.com 80 --service frontend -t web`.

Containers started separately can join the network: `docker run -d --network cloudify --name api my-api-image`
or `docker network connect cloudify api`.

## The command line inside the container

Every `cloudify` command works through `docker exec`:

```bash
docker exec -it cloudify cloudify tunnel list
docker exec -it cloudify cloudify account connect example.com     # prints the authorization link
docker exec -it cloudify cloudify route add app.example.com 3000 --service frontend -t web
docker exec -it cloudify cloudify replica import /root/.cloudflared/web-replica.json
docker exec -it cloudify cloudify -ssh host                         # share the *container's* shell
```

To copy a file in (a replica bundle, for example): `docker cp web-replica.json cloudify:/root/.cloudflared/`.

## Start order inside the container

`docker/entrypoint.sh` runs at every container start:

1. `cron`: keeps `@reboot` auto-start entries working;
2. `cloudify tunnel resume`: starts every tunnel with auto-start enabled (the list is in the volume, so this
   also works in a brand-new container after an upgrade);
3. `exec cloudify serve`: the dashboard, as the container's main process. The image's health check
   requests `http://localhost:8765/healthz` every 30 seconds.

```bash
docker ps                                                   # STATUS shows (healthy)
docker inspect --format '{{.State.Health.Status}}' cloudify
docker logs -f cloudify                                     # dashboard log
docker exec cloudify ls /root/.cloudflared/logs             # one log per tunnel
```

## Updating

```bash
cd Cloudify_v2
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

The volume keeps all data; the new container resumes the auto-start tunnels. Downtime is the few seconds the
container needs to restart. Clean up old image layers afterwards with `docker image prune`.

### Moving from the Cloudify 1.x container

The 1.x instructions ran `docker run -dit -p 8765:8765 --name cloudify vickydev810/cloudify:latest`: no data
volume, so everything lives inside that container, and dashboard accounts are in `/app/users.json`. Copy the
data out **before** removing it. Expect about a minute without tunnels while you switch:

```bash
docker rename cloudify cloudify-1x            # frees the name "cloudify" for the new container
docker cp cloudify-1x:/root/.cloudflared ./cloudflared-data
docker cp cloudify-1x:/app/users.json ./users.json 2>/dev/null || true
docker stop cloudify-1x

# put the data into the new volume (the shipped compose file names it cloudify_cloudify-data)
docker volume create cloudify_cloudify-data
docker run --rm -v cloudify_cloudify-data:/data -v "$PWD":/src alpine sh -c '
  cp -a /src/cloudflared-data/. /data/ &&
  if [ -f /src/users.json ]; then mkdir -p /data/cloudify && cp /src/users.json /data/cloudify/users.json &&
     chmod 600 /data/cloudify/users.json; fi'

docker compose -f docker/docker-compose.yml up -d --build
```

Tunnels that had auto-start enabled start by themselves; start the others in the dashboard. When everything
works, remove the old container (`docker rm cloudify-1x`) and the copies: `cloudflared-data/` contains tunnel
secrets (`rm -rf cloudflared-data users.json`). Until then you can go back: stop the new container
(`docker compose -f docker/docker-compose.yml stop`) and run `docker start cloudify-1x`.

## Backup and restore

```bash
# backup (contains tunnel secrets: store it safely)
docker run --rm -v cloudify_cloudify-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/cloudify-backup.tar.gz -C /data .

# restore into a new or empty volume
docker run --rm -v cloudify_cloudify-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/cloudify-backup.tar.gz -C /data
```

Use `docker volume ls` to see the volume's exact name (`cloudify_cloudify-data` with the shipped compose file,
`cloudify-data` with the `docker run` command above).

## Securing the dashboard

The compose file publishes the dashboard on `127.0.0.1` only, so it is reachable from the Docker host itself.
To use it from your laptop, pick one:

* **SSH tunnel** (nothing to change): `ssh -L 8765:localhost:8765 user@server`, then open `http://localhost:8765`.
* **HTTPS**: mount a certificate and key and pass them to the dashboard, then publish the port on the network:

  ```yaml
      environment:
        CLOUDIFY_SERVE_ARGS: "--ssl-certfile /certs/cert.pem --ssl-keyfile /certs/key.pem"
      volumes:
        - ./certs:/certs:ro
      ports:
        - "8765:8765"
  ```

* **Cloudflare Access**: add a route `dash.example.com → localhost:8765` on a tunnel and protect that hostname
  with a Cloudflare Access policy (Zero Trust → Access → Applications); start the dashboard with
  `CLOUDIFY_SERVE_ARGS="--insecure-http --allowed-host dash.example.com"`.
* **Plain HTTP on a trusted network**: change the mapping to `"8765:8765"`. Passwords and login tokens then cross
  the network unencrypted.

Creating the first account needs the setup code from `docker logs cloudify`, so nobody else on the network can
claim a new installation. See [Security](security.md) for more.

## Removing

```bash
docker compose -f docker/docker-compose.yml down          # stops and removes the container, keeps the data
docker compose -f docker/docker-compose.yml down -v       # ALSO deletes the volume: logins, tunnels, users
```

Tunnels and DNS records in your Cloudflare account are not deleted by removing the container. Delete tunnels
first (dashboard or `docker exec cloudify cloudify tunnel delete <name>`) if you no longer need them.

## Platform notes

* **Synology / QNAP / Unraid / TrueNAS:** use the compose file in their *Container Manager* / *Compose*
  feature, or `docker run` with a volume or a host folder (e.g. `-v /volume1/docker/cloudify:/root/.cloudflared`).
* **Raspberry Pi:** build on the Pi (`docker compose ... up -d --build`) or with `buildx` for `linux/arm64`
  (64-bit OS) or `linux/arm/v7` (32-bit OS).
* **Docker Desktop (macOS/Windows):** works as is; use `host.docker.internal` to reach services running
  on your computer.
* **Rootless Docker / Podman:** work as well; `podman compose` reads the same file. Publishing ports
  below 1024 is not needed (8765).
