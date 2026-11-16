# Cloudify in Docker

Everything Docker-specific lives in this folder. For installing directly on Linux or macOS, use
[`install.sh`](../install.sh) in the repository root instead.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Image: Python 3.12, cloudflared (pinned, per architecture), Cloudify, cron, OpenSSH client, health check |
| `entrypoint.sh` | Container start: cron, resume auto-start tunnels, then the dashboard |
| `docker-compose.yml` | Recommended way to run it (restart policy + data volume) |
| `Dockerfile.dockerignore` | Files left out of the build context |

All commands below run from the **repository root**.

## Run with Docker Compose (recommended)

```bash
docker compose -f docker/docker-compose.yml up -d --build
docker logs cloudify #to get the setup code
```

Dashboard: http://localhost:8765, http://{Your server ip}:8765. Includes `restart: unless-stopped`, so the container and every tunnel with
auto-start come back after a server reboot (make sure Docker itself starts at boot:
`sudo systemctl enable docker`).

## Run with `docker run`

```bash
docker build -f docker/Dockerfile -t cloudify:latest .
docker run -d --restart unless-stopped -p 127.0.0.1:8765:8765 \
  -v cloudify-data:/root/.cloudflared --name cloudify cloudify:latest
```

* `-v cloudify-data:/root/.cloudflared` keeps your Cloudflare logins, tunnel credentials, configs and Cloudify
  accounts when the container is recreated (e.g. when upgrading the image).
* Restart policies belong to the container, not the image, which is why `--restart` must be given here
  (or `restart:` in compose).

## Build the image

```bash
docker build -f docker/Dockerfile -t cloudify:latest .
docker build -f docker/Dockerfile --build-arg CLOUDFLARED_VERSION=latest -t cloudify:latest .
docker buildx build -f docker/Dockerfile --platform linux/amd64,linux/arm64 -t cloudify:latest .
```

## Reach other containers

Put Cloudify and your services on the same network and use the container name as the route's service host:

```bash
docker network create my-net
docker run -d --restart unless-stopped -p 127.0.0.1:8765:8765 -v cloudify-data:/root/.cloudflared \
  --name cloudify --network my-net cloudify:latest
docker run -d --restart unless-stopped --name frontend --network my-net <your-image>
```

Then add a route `app.example.com → frontend : 3000`. The compose file already defines a `cloudify` network.

## Use the CLI inside the container

```bash
docker exec -it cloudify cloudify tunnel list
docker exec -it cloudify cloudify -ssh host      # share the container's shell temporarily
```

## Upgrading

```bash
git pull
docker compose -f docker/docker-compose.yml up -d --build
```

The data volume is kept, and on start the container runs `cloudify tunnel resume`, which starts every
auto-start tunnel and restores its auto-start entry.

## How the container starts (`entrypoint.sh`)

1. `cron`: keeps `@reboot` auto-start entries working
2. `cloudify tunnel resume`: starts tunnels with auto-start enabled (state is in the volume)
3. `exec cloudify serve`: the dashboard (PID 1); the health check polls it

Tests: `CLOUDIFY_DOCKER_TESTS=1 pytest tests/test_docker.py` builds this image and checks restart and
recreate-from-volume behaviour.

More (routing to services on the host, moving from the 1.x container, backups, NAS/Raspberry Pi notes):
[docs/docker.md](../docs/docker.md).
