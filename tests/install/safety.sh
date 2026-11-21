#!/usr/bin/env bash
# Runs inside a fresh container (never on a real machine: it stops tunnels): checks safety-check.sh.
#   safety.sh kill      list and stop stand-in tunnels (no Cloudify installed)
#   safety.sh remove    install Cloudify, add auto-start entries and data, then remove everything
set -uo pipefail
MODE="${1:-kill}"
STANDIN=/src/tests/install/standin
CHECK=/src/safety-check.sh
SECRET="SECRET-TUNNEL-TOKEN-$$"
fails=0
expect() { if printf '%s' "$2" | grep -qF -- "$3"; then :; else echo "MISSING ($1): $3"; fails=$((fails + 1)); fi; }
refuse() { if printf '%s' "$2" | grep -qF -- "$3"; then echo "UNEXPECTED ($1): $3"; fails=$((fails + 1)); fi; }
running() {  # running TEXT: is a process with TEXT in its command line running?
    local f
    for f in /proc/[0-9]*/cmdline; do
        tr '\0' ' ' < "$f" 2>/dev/null | grep -qF -- "$1" && return 0
    done
    return 1
}

start_standins() {
    mkdir -p /tmp/bin && cp "$STANDIN/cloudflared" "$STANDIN/cloudify" /tmp/bin/
    setsid /tmp/bin/cloudify -p 8000 >/dev/null 2>&1 < /dev/null &
    setsid /tmp/bin/cloudflared tunnel --url ssh://127.0.0.1:2222 >/dev/null 2>&1 < /dev/null &
    setsid /tmp/bin/cloudflared tunnel --metrics 127.0.0.1:20312 --config "$HOME/.cloudflared/config-web.yml" run web \
        >/dev/null 2>&1 < /dev/null &
    setsid /tmp/bin/cloudflared tunnel run --token "$SECRET" >/dev/null 2>&1 < /dev/null &
    sleep 2
}

if [ "$MODE" = kill ]; then
    echo "bash $BASH_VERSION"
    start_standins
    out="$(bash "$CHECK" 2>&1)"; echo "$out"
    expect list "$out" "https://stand-in-20311.trycloudflare.com -> http://localhost:8000"
    expect list "$out" "started by: cloudify -p"
    expect list "$out" "SSH tunnel -> ssh://127.0.0.1:2222"
    expect list "$out" "tunnel web, 4 Cloudflare connections"
    expect list "$out" "(started with a token)"
    refuse list "$out" "$SECRET"
    out="$(bash "$CHECK" --kill 2>&1)"; echo "$out"
    expect kill "$out" "no temporary tunnels or SSH sessions are running"
    running "ssh://127.0.0.1:2222" && { echo "SSH tunnel still running"; fails=$((fails + 1)); }
    running "run web" || { echo "--kill stopped a persistent tunnel"; fails=$((fails + 1)); }
    out="$(bash "$CHECK" --kill-all < /dev/null 2>&1)"
    expect no-confirmation "$out" "run again with --yes"
    running "run web" || { echo "--kill-all stopped tunnels without confirmation"; fails=$((fails + 1)); }
    out="$(bash "$CHECK" --kill-all --yes 2>&1)"; echo "$out"
    expect kill-all "$out" "no persistent tunnels or dashboards are running"
    refuse kill-all "$out" "$SECRET"
    running "run web" && { echo "persistent tunnel still running"; fails=$((fails + 1)); }

elif [ "$MODE" = remove ]; then
    export PATH="$HOME/.local/bin:$PATH"
    bash /src/install.sh --local /src > /tmp/install.log 2>&1 || { tail -20 /tmp/install.log; exit 1; }
    if ! command -v cloudify >/dev/null || ! command -v cloudflared >/dev/null; then echo "install failed"; exit 1; fi
    mkdir -p "$HOME/.cloudflared/cloudify" "$HOME/.local/bin"
    echo "fake" > "$HOME/.cloudflared/cert.pem"
    echo '{"TunnelSecret":"x"}' > "$HOME/.cloudflared/11111111-2222-3333-4444-555555555555.json"
    printf '#!/bin/bash\nexit 0\n' > "$HOME/.local/bin/start-tunnel-web.sh"
    chmod +x "$HOME/.local/bin/start-tunnel-web.sh"
    printf '@reboot %s\n0 3 * * * echo unrelated-job\n' "$HOME/.local/bin/start-tunnel-web.sh" | crontab -
    start_standins
    setsid cloudify serve --serve-port 18766 >/dev/null 2>&1 < /dev/null &
    sleep 3
    out="$(bash "$CHECK" 2>&1)"; echo "$out"
    expect list "$out" "crontab: @reboot $HOME/.local/bin/start-tunnel-web.sh"
    # open to the network by default: listed as reachable, with the plain-HTTP warning and the --secure hint
    expect list "$out" "listening on 0.0.0.0:18766 - reachable from the network"
    expect list "$out" "plain HTTP: passwords and logins cross the network unencrypted"
    expect list "$out" "restart it with --secure"
    expect list "$out" "cloudflared "
    out="$(bash "$CHECK" --remove < /dev/null 2>&1)"
    expect no-confirmation "$out" "run again with --yes"
    command -v cloudify >/dev/null || { echo "--remove removed Cloudify without confirmation"; fails=$((fails + 1)); }
    out="$(bash "$CHECK" --remove --purge --yes 2>&1)"; echo "$out"
    refuse remove "$out" "$SECRET"
    hash -r
    command -v cloudify && { echo "LEFT: cloudify"; fails=$((fails + 1)); }
    command -v cloudflared && { echo "LEFT: cloudflared"; fails=$((fails + 1)); }
    [ -e "$HOME/.cloudflared" ] && { echo "LEFT: ~/.cloudflared"; fails=$((fails + 1)); }
    [ -e "$HOME/.local/bin/start-tunnel-web.sh" ] && { echo "LEFT: start script"; fails=$((fails + 1)); }
    crontab -l 2>/dev/null | grep -q start-tunnel && { echo "LEFT: cron entry"; fails=$((fails + 1)); }
    crontab -l 2>/dev/null | grep -q unrelated-job || { echo "LOST: an unrelated cron job"; fails=$((fails + 1)); }
    running "cloudify serve" && { echo "LEFT: dashboard"; fails=$((fails + 1)); }
    running "/tmp/bin/cloudflared" && { echo "LEFT: tunnels"; fails=$((fails + 1)); }
fi

if [ "$fails" -eq 0 ]; then echo "SAFETY-CHECK-OK"; else echo "$fails problem(s)"; exit 1; fi
