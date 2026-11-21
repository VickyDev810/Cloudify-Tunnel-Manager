# Docker

Run Cloudify in a container on any machine with Docker: Linux servers, NAS devices (Synology, QNAP, Unraid,
TrueNAS SCALE), Raspberry Pi, or Docker Desktop on macOS and Windows. The image contains Python, Cloudify,
cloudflared (for the image's CPU architecture), cron and the OpenSSH client. All Docker files are in
[`docker/`](../docker/README.md); run the commands below from the repository root.

## Quick start with Docker Compose (recommended)

```bash
git clone https://github.com/VickyDev810/Cloudify-Tunnel-Manager.git
cd Cloudify-Tunnel-Manager
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
| `network_mode: host` | The container uses this machine's network. In a route, `localhost` is this machine, so services on the host and the published ports of all other containers are reachable as `localhost:PORT` ([routing](#routing-to-your-services)). |
| dashboard on port 8765 | It listens on every interface of this machine, over plain HTTP, under any address. `CLOUDIFY_SECURE=1` locks it down (see *Securing the dashboard*). |

Make sure Docker itself starts at boot: `sudo systemctl enable docker` (Docker Desktop: *Start Docker Desktop
when you sign in*).

## Without Compose

```bash
docker build -f docker/Dockerfile -t cloudify:latest .
docker run -d --name cloudify --restart unless-stopped --network host \
  -v cloudify-data:/root/.cloudflared cloudify:latest
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

With the included compose file the container uses **host networking**, so a route's *service host* is resolved
on this machine: `localhost` is the Docker host.

| Service runs... | Service host and port in the route | Setup |
| --- | --- | --- |
| on the Docker host | `localhost` and its port | Nothing |
| in another container | `localhost` and the port it publishes on the host, e.g. `ports: ["7631:80"]` → `localhost`, `7631` | Publish its port |
| on another machine in your LAN | its IP, e.g. `192.168.1.20` | Nothing |

Example: an app container next to Cloudify.

```yaml
# docker/docker-compose.yml (excerpt)
services:
  cloudify:
    # ... as shipped ...
  frontend:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:80"   # Cloudify reaches it on localhost:8080; your network can't reach it directly
```

Then add the route `app.example.com → localhost : 8080` in the dashboard, or
`docker exec cloudify cloudify route add app.example.com 8080 -t web`.

Containers started separately work the same way: `docker run -d -p 127.0.0.1:8080:80 --name api my-api-image`,
then route to `localhost`, port `8080`.

### Bridge networking instead

Use bridge networking on Docker Desktop without host networking, or to keep the Cloudify container in a
network of its own. Replace `network_mode: host` in the compose file with:

```yaml
    ports:
      - "127.0.0.1:8765:8765"
    networks:
      - cloudify
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Then add `networks: { cloudify: { name: cloudify } }` at the end of the file (`docker run`: `-p 127.0.0.1:8765:8765
--add-host host.docker.internal:host-gateway` instead of `--network host`).

With bridge networking, `localhost` in a route is the Cloudify container itself:

* for a service on the Docker host, use `host.docker.internal`;
* for another container, use its name and put it on the same network (`networks: [cloudify]`, or
  `docker network connect cloudify api`).

The published port decides who can reach the dashboard: `127.0.0.1:8765:8765` above means this machine only.
Secure mode (`CLOUDIFY_SECURE=1`) makes it listen on the container's own `localhost`. The published port then
can't reach it at all, only a tunnel route in the container, as in the Cloudflare Access setup below.

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
cd Cloudify-Tunnel-Manager
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

By default the dashboard is open. It listens on port 8765 of every interface of this machine, over plain HTTP,
and answers under any address: the server's public or private IP, a domain, or a quick tunnel. On AWS or Azure,
allow TCP port 8765 in the security group or network security group to reach it by public IP. Anyone who can
reach the port can open the login page, and passwords and login tokens cross the network unencrypted. Creating
the first account needs the setup code from `docker logs cloudify`, so nobody else can claim a new installation.

To lock it down or encrypt it:

* **Secure mode:** the dashboard listens on this machine only (`127.0.0.1`) and answers only to known names.
  Put the setting in `docker/.env`, next to the compose file (never committed, so `git pull` keeps working),
  and recreate the container:

  ```bash
  echo 'CLOUDIFY_SECURE=1' >> docker/.env
  docker compose -f docker/docker-compose.yml up -d
  ```

  Or change `CLOUDIFY_SECURE: ${CLOUDIFY_SECURE:-0}` to `CLOUDIFY_SECURE: "1"` in the compose file. From your
  laptop, use an SSH tunnel: `ssh -L 8765:localhost:8765 user@server`, then open `http://localhost:8765`.
* **HTTPS:** mount a certificate and a key:

  ```yaml
      environment:
        CLOUDIFY_SERVE_ARGS: "--ssl-certfile /certs/cert.pem --ssl-keyfile /certs/key.pem"
      volumes:
        - cloudify-data:/root/.cloudflared
        - ./certs:/certs:ro
  ```

* **Cloudflare Access:**
  1. On a tunnel, add a route `dash.example.com → localhost:8765`.
  2. Protect that hostname with a Cloudflare Access policy (Zero Trust → Access → Applications).
  3. Turn on secure mode. The names of your routes are known names, so `dash.example.com` works.

  The tunnel reaches the dashboard on `localhost`; nobody on your network can.
* **A firewall rule** that allows port 8765 only from the machines that need it. On AWS or Azure, allow only
  your own IP address in the security group.

### Opening the dashboard through a quick tunnel

A quick tunnel, for example `docker exec -it cloudify cloudify -p 8765`, gives the dashboard a public
`*.trycloudflare.com` address. By default that just works.

In secure mode the dashboard refuses names it doesn't know (*This dashboard runs in secure mode and can't be
opened as ...*), and a quick tunnel gets a new random name every time it starts. To allow every quick tunnel,
add this to `docker/.env` and recreate the container:

```bash
echo 'CLOUDIFY_ALLOWED_HOSTS=*.trycloudflare.com' >> docker/.env
docker compose -f docker/docker-compose.yml up -d
```

Anyone who has a quick tunnel's address can open the login page, so stop the quick tunnel when you're done. For
lasting access, a route on your own domain with Cloudflare Access (above) is safer.

See [Security](security.md) for more.

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
* **Docker Desktop (macOS/Windows):** host networking needs Docker Desktop 4.34 or later, with *Enable host
  networking* turned on (Settings → Resources → Network). Otherwise use [bridge
  networking](#bridge-networking-instead) and `host.docker.internal` for services running on your computer.
* **Rootless Docker / Podman:** work as well; `podman compose` reads the same file. With rootless Docker,
  host networking uses the rootless network namespace. If routes to `localhost` don't reach your services
  there, use [bridge networking](#bridge-networking-instead).
