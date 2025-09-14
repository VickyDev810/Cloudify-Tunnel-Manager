FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    curl \
    procps \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install cloudflared
RUN curl -fsSL -o /usr/local/bin/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
 && chmod +x /usr/local/bin/cloudflared

# Set working directory
WORKDIR /app

# Copy your project
COPY . /app

# Install your project
RUN pip3 install .

# Copy entrypoint script
COPY service.sh /service.sh
RUN chmod +x /service.sh

# Expose port
EXPOSE 8765

# Run entrypoint
CMD ["/service.sh"]
