#!/usr/bin/env bash
# Kept so existing install links keep working: the installer is now install.sh in the repository
# root (one script for Linux and macOS). Options are passed through, e.g. --update or --branch NAME.
# It always downloads the official installer: running an install.sh found next to the working
# directory could run a file someone else put there.
set -euo pipefail
URL="https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" | bash -s -- "$@"
else
    wget -qO- "$URL" | bash -s -- "$@"
fi
