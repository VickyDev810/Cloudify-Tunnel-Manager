#!/usr/bin/env bash
# Runs inside a fresh container: fake a Cloudify 1.x installation with its data, run
# `install.sh --update` and check that the program was replaced and everything else was kept.
#   update.sh pip-user    1.x installed with `pip install --user` (its fallback when pipx failed)
#   update.sh pipx        1.x installed with pipx
set -euo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND"' ERR
KIND="${1:-pip-user}"
export PATH="$HOME/.local/bin:$PATH"
NEW_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' /src/cloudify/__init__.py)"

echo "### fake Cloudify 1.0.0 ($KIND)"
OLD="$(mktemp -d)"
mkdir -p "$OLD/cli/frontend" "$OLD/core"
cat > "$OLD/setup.py" <<'EOF'
from setuptools import setup
setup(name="cloudify-cli", version="1.0.0", packages=["cli", "core"],
      package_data={"cli": ["frontend/*"]},
      entry_points={"console_scripts": ["cloudify=cli.cli:cli"]})
EOF
: > "$OLD/cli/__init__.py"
: > "$OLD/core/__init__.py"
cat > "$OLD/cli/cli.py" <<'EOF'
import sys, time
def cli():
    if sys.argv[1:] == ["--version"]:
        print("cloudify, version 1.0.0")
    elif sys.argv[1:2] == ["serve"]:
        time.sleep(600)
EOF
echo '{"name": "frontend"}' > "$OLD/cli/frontend/package.json"
if [ "$KIND" = pipx ]; then
    pipx install "$OLD" >/dev/null 2>&1
else
    python3 -m pip install --user --no-deps --break-system-packages --quiet "$OLD" 2>/dev/null \
        || python3 -m pip install --user --no-deps --quiet "$OLD"
    SITE="$(python3 -m site --user-site)"
    # what the 1.x dashboard's `npm install` left behind
    mkdir -p "$SITE/cli/frontend/node_modules/next"
    echo "module.exports = {}" > "$SITE/cli/frontend/node_modules/next/index.js"
fi
hash -r
[ "$(cloudify --version)" = "cloudify, version 1.0.0" ]

# 1.x data: tunnels in ~/.cloudflared, dashboard accounts in the folder `cloudify serve` ran in
mkdir -p "$HOME/.cloudflared" "$HOME/work"
cat > "$HOME/.cloudflared/tunnel_manager_state.json" <<'EOF'
{"tunnels": {"web": {"created_at": "2025-10-01T10:00:00", "config_file": "/x/config-web.yml", "routes": [], "auto_start": false, "status": "unknown"}, "api": {"created_at": "2025-10-02T10:00:00", "config_file": "/x/config-api.yml", "routes": [], "auto_start": true, "status": "unknown"}}, "current_tunnel": "web"}
EOF
printf 'tunnel: web\ningress:\n  - service: http_status:404\n' > "$HOME/.cloudflared/config-web.yml"
cp "$HOME/.cloudflared/tunnel_manager_state.json" /tmp/state.before
cat > "$HOME/work/users.json" <<'EOF'
{"admin": {"username": "admin", "hashed_password": "$2b$12$abcdefghijklmnopqrstuuJ0J8k9mP0v1yYb2nq9VbW5yGgS6Gq1C", "is_admin": true}}
EOF
chmod 600 "$HOME/work/users.json"
(cd "$HOME/work" && exec cloudify serve) &
OLD_DASHBOARD=$!
sleep 1

echo "### install.sh --update"
bash /src/install.sh --update --local /src 2>&1 | tee /tmp/update.log
hash -r

echo "### verify"
grep -q "Cloudify 1.0.0 (installed with: $KIND)" /tmp/update.log
grep -q "Cloudify updated: 1.0.0 -> $NEW_VERSION" /tmp/update.log
grep -q "Settings backed up to" /tmp/update.log
grep -q "Dashboard accounts moved from ~/work/users.json" /tmp/update.log
grep -q "The dashboard is still running the previous version (process $OLD_DASHBOARD)" /tmp/update.log
[ "$(cloudify --version | awk '{print $NF}')" = "$NEW_VERSION" ]
cloudify tunnel list | grep -q "^api "
cmp "$HOME/.cloudflared/tunnel_manager_state.json" /tmp/state.before    # tunnels untouched
cmp <(python3 -m json.tool "$HOME/work/users.json") <(python3 -m json.tool "$HOME/.cloudflared/cloudify/users.json")
[ "$(stat -c %a "$HOME/.cloudflared/cloudify/users.json")" = 600 ]
BACKUP="$(ls "$HOME"/.cloudflared/cloudify/backups/before-update-*.tar.gz)"
tar -tzf "$BACKUP" | grep -qx tunnel_manager_state.json
tar -tzf "$BACKUP" | grep -qx config-web.yml
[ "$(stat -c %a "$BACKUP")" = 600 ]
if [ "$KIND" = pip-user ]; then
    for leftover in "$SITE/cli" "$SITE/core" "$SITE"/cloudify_cli-1.0.0.dist-info; do
        if [ -e "$leftover" ]; then echo "left behind: $leftover"; exit 1; fi
    done
    grep -q "Removed the old copy installed with pip --user" /tmp/update.log
fi
kill "$OLD_DASHBOARD" 2>/dev/null || true

echo "### update again (nothing old left)"
bash /src/install.sh --update --local /src --skip-cloudflared > /tmp/update2.log 2>&1
grep -q "Cloudify updated: $NEW_VERSION -> $NEW_VERSION" /tmp/update2.log
if grep -q "pip --user\|still running" /tmp/update2.log; then cat /tmp/update2.log; exit 1; fi
echo "UPDATE-CHECK-OK"
