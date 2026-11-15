#!/usr/bin/env bash
# Runs inside a fresh container: install Cloudify with install.sh and check it works.
#   check.sh local          install from the mounted checkout (/src)
#   check.sh git BRANCH     install by cloning /src (exercises the git path)
set -euo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND"' ERR
MODE="${1:-local}"
if [ "$MODE" = git ]; then
    # /src belongs to another user (e.g. the CI runner), which git refuses to clone ("dubious
    # ownership"). git isn't installed yet (the installer adds it), so `git config` can't be used:
    # write the setting into the user's git configuration directly.
    printf '[safe]\n\tdirectory = *\n' >> "$HOME/.gitconfig"
    ARGS=(--repo /src --branch "$2")
else
    ARGS=(--local /src)
fi

echo "### install"
bash /src/install.sh "${ARGS[@]}"
export PATH="$HOME/.local/bin:$PATH"

echo "### verify"
cloudify --version
cloudflared --version
cloudify tunnel list >/dev/null
cloudify serve --serve-port 18765 > /tmp/serve.log 2>&1 &
for _ in $(seq 40); do
    if curl -fs http://127.0.0.1:18765/setup/check > /tmp/check.json 2>/dev/null; then break; fi
    sleep 0.5
done
grep -q '"needs_setup":true' /tmp/check.json
curl -fs http://127.0.0.1:18765/ | grep -q "js/app.js"
kill %1 2>/dev/null || true

echo "### run again (update path)"
bash /src/install.sh "${ARGS[@]}" --skip-cloudflared >/dev/null
cloudify --version >/dev/null

echo "### uninstall"
bash /src/install.sh --uninstall
hash -r
if command -v cloudify >/dev/null 2>&1; then echo "cloudify still installed"; exit 1; fi
echo "INSTALL-CHECK-OK"
