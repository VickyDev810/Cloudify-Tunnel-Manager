#!/bin/bash
# Container entrypoint
set -e

# cron keeps @reboot auto-start entries working inside the container
cron

# Start every tunnel with auto-start enabled. The list lives in the
# /root/.cloudflared volume, so this also works after the container is
# recreated (e.g. when upgrading the image), when the old crontab is gone.
cloudify tunnel resume || echo "Warning: could not resume auto-start tunnels"

# Web UI + API on port 8765. By default on every interface, under any address (an IP address, a domain or a
# quick tunnel). With docker-compose.yml (host networking) these are this machine's interfaces; with a
# published port (-p), the port mapping decides who can reach it. CLOUDIFY_SECURE=1 locks it down: this
# machine only, known names only. CLOUDIFY_SERVE_ARGS adds options, e.g.
# "--ssl-certfile /certs/cert.pem --ssl-keyfile /certs/key.pem" (HTTPS).
# shellcheck disable=SC2086  # CLOUDIFY_SERVE_ARGS is a list of options
exec cloudify serve ${CLOUDIFY_SERVE_ARGS:-}
