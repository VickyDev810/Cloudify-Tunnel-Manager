#!/bin/bash
set -e

# Start tunnels first
for script in /root/.local/bin/start-tunnel-*.sh; do
    if [ -x "$script" ]; then
        "$script" &
    fi
done

## Start cron daemon
cron -f &


# Start your Python app
exec cloudify serve
