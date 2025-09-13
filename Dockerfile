FROM python:3.11-slim

ENV container docker
ENV DEBIAN_FRONTEND=noninteractive
STOPSIGNAL SIGRTMIN+3

# Install dependencies including systemd
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    systemd \
    curl \
    cron \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install cloudflared
RUN curl -fsSL -o /usr/local/bin/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
 && chmod +x /usr/local/bin/cloudflared

# Set working directory
WORKDIR /app

# Copy your project into the container
COPY . /app

# Install your project
RUN pip3 install .

#Setup service.sh
RUN chmod +x service.sh


# Expose the service port
EXPOSE 8765

# Start my app
CMD ["/app/service.sh"]

# CMD ["service", "cron", "start", "&&", "cloudify", "serve"]


