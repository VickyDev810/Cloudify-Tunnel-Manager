# Cloudify in Docker. Build from the repository root:
#   docker build -f docker/Dockerfile -t cloudify .
# or use docker/docker-compose.yml (see docker/README.md).
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# cloudflared release to install. Override with:
#   docker build --build-arg CLOUDFLARED_VERSION=latest .
ARG CLOUDFLARED_VERSION=2026.11.0
# Set automatically by `docker buildx` (amd64, arm64, arm, 386)
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    curl \
    ca-certificates \
    procps \
    openssh-client \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install cloudflared for the image's architecture (previously always amd64,
# which broke images built on / for ARM such as Apple Silicon and Raspberry Pi),
# checked against the SHA-256 that Cloudflare publishes in the release notes.
RUN set -eux; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
      amd64|arm64|386) cf_arch="$arch" ;; \
      arm|armhf) cf_arch="arm" ;; \
      i386) cf_arch="386" ;; \
      *) echo "Unsupported architecture: $arch"; exit 1 ;; \
    esac; \
    file="cloudflared-linux-${cf_arch}"; \
    api="https://api.github.com/repos/cloudflare/cloudflared/releases"; \
    if [ "$CLOUDFLARED_VERSION" = "latest" ]; then \
      release="$(curl -fsSL "$api/latest" || true)"; \
    else \
      release="$(curl -fsSL "$api/tags/${CLOUDFLARED_VERSION}" || true)"; \
    fi; \
    tag="$(printf '%s' "$release" | python3 -I -c 'import json, sys; print(json.load(sys.stdin).get("tag_name") or "")' 2>/dev/null || true)"; \
    tag="${tag:-$CLOUDFLARED_VERSION}"; \
    if [ "$tag" = "latest" ]; then \
      url="https://github.com/cloudflare/cloudflared/releases/latest/download/${file}"; \
    else \
      url="https://github.com/cloudflare/cloudflared/releases/download/${tag}/${file}"; \
    fi; \
    curl -fsSL -o /tmp/cloudflared "$url"; \
    expected="$(printf '%s' "$release" | python3 -I -c 'import json, re, sys; body = json.load(sys.stdin).get("body") or ""; m = re.search(r"^" + re.escape(sys.argv[1]) + r":\s*([0-9a-f]{64})\s*$", body, re.M); print(m.group(1) if m else "")' "$file" 2>/dev/null || true)"; \
    if [ -n "$expected" ]; then \
      echo "$expected  /tmp/cloudflared" | sha256sum -c - \
        || { echo "cloudflared $tag does not match its published SHA-256; refusing to install it"; exit 1; }; \
    else \
      echo "WARNING: no published SHA-256 found for $file ($tag); installed without checksum verification"; \
    fi; \
    install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared; \
    rm -f /tmp/cloudflared; \
    cloudflared --version

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip3 install -r requirements.txt

COPY . /app
RUN pip3 install .

COPY docker/entrypoint.sh /usr/local/bin/cloudify-entrypoint
RUN chmod +x /usr/local/bin/cloudify-entrypoint

# Tunnel configs, credentials and Cloudify users live here; mount a volume
# to keep them across container re-creation.
VOLUME ["/root/.cloudflared"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fs http://localhost:8765/healthz > /dev/null || curl -fsk https://localhost:8765/healthz > /dev/null || exit 1

CMD ["cloudify-entrypoint"]
