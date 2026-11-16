#!/usr/bin/env bash
# Cloudify installer for Linux and macOS (without Docker; for Docker see docker/).
#
#   curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --branch my-branch
#   curl -fsSL .../install.sh | bash -s -- --update     # update, also from Cloudify 1.x
#   bash install.sh --local .          # install from a local checkout
#   bash install.sh --uninstall
#
# What it does (asks no questions, safe to run again to update):
#   1. installs missing prerequisites (python3 >= 3.8, pipx or venv, git, curl) with the system
#      package manager; uses sudo only for that and for /usr/local/bin
#   2. installs cloudflared, or updates it when an older version is found
#   3. installs Cloudify into its own isolated environment (pipx, or a virtualenv as fallback);
#      an existing installation (any version) is replaced after a backup of its settings, and
#      leftovers of 1.x (a `pip install --user` copy, users.json in a working folder) are moved over
#   4. makes sure `cloudify` is on PATH and checks that it runs

set -euo pipefail
ORIGINAL_PATH="$PATH"   # this script adds ~/.local/bin to its own PATH; the user's shell may not have it

REPO_URL="https://github.com/yash7158/Cloudify_v2.git"
BRANCH="main"
LOCAL_SRC=""
SKIP_CLOUDFLARED=false
UNINSTALL=false
UPDATE=false
PURGE=false
VERBOSE=false
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/cloudify/venv"
BIN_DIR="$HOME/.local/bin"

# ------------------------------------------------------------------ output

if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi
step()  { printf '\n%s==>%s %s%s%s\n' "$BLUE" "$RESET" "$BOLD" "$*" "$RESET"; }
ok()    { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
info()  { printf '  %s\n' "$*"; }
warn()  { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()   { printf '\n%s✗ %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
run()   { if $VERBOSE; then "$@"; else "$@" >/dev/null 2>&1; fi; }
have()  { command -v "$1" >/dev/null 2>&1; }
fetch() {  # fetch URL [FILE]: download with curl, or wget where curl is missing (to stdout without FILE)
    if have curl; then
        if [ -n "${2:-}" ]; then curl -fsSL -o "$2" "$1"; else curl -fsSL "$1"; fi
    else
        if [ -n "${2:-}" ]; then wget -q -O "$2" "$1"; else wget -q -O - "$1"; fi
    fi
}
PY="python3"   # the Python used for everything below; find_python may pick a newer python3.X
find_python() {  # python3 if it is 3.8+, else the newest python3.X >= 3.8 (RHEL 8 and Ubuntu 18.04 ship 3.6)
    local c
    for c in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
        if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
            PY="$(command -v "$c")"
            return 0
        fi
    done
    return 1
}
# shellcheck disable=SC2088  # a literal ~ for display
tildify() { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }

usage() {
    cat <<EOF
Cloudify installer (Linux, macOS)

Usage: install.sh [options]

  --branch NAME       install this branch/tag of the repository (default: main)
  --repo URL          install from another git repository (or a local repo path)
  --local DIR         install from a local checkout (no git needed)
  --skip-cloudflared  don't install or update cloudflared
  --update            update an existing installation to this version, including one made
                      by Cloudify 1.x (keeps tunnels, settings and dashboard accounts;
                      running tunnels are not interrupted)
  --uninstall         remove Cloudify (keeps your tunnels and settings)
  --purge             with --uninstall: also delete ~/.cloudflared/cloudify (accounts, users)
  --verbose           show the output of every command
  -h, --help          show this help

Docker users: see docker/README.md instead.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
        --repo) REPO_URL="${2:?--repo needs a value}"; shift 2 ;;
        --local) LOCAL_SRC="${2:?--local needs a directory}"; shift 2 ;;
        --skip-cloudflared) SKIP_CLOUDFLARED=true; shift ;;
        --update|update) UPDATE=true; shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        --purge) PURGE=true; shift ;;
        --verbose|-v) VERBOSE=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown option: $1" ;;
    esac
done

# Python's `-`, `-c` and `-m` put the working directory first on its import path, and pip/pipx
# build from paths: resolve the user's paths, then work from / so that files someone else left
# in the folder the installer was started from (e.g. /tmp) can never be imported or run.
ORIG_PWD="$PWD"
if [ -n "$LOCAL_SRC" ]; then LOCAL_SRC="$(cd "$LOCAL_SRC" 2>/dev/null && pwd -P)" || die "No such folder: $LOCAL_SRC"; fi
if [ -d "$REPO_URL" ]; then REPO_URL="$(cd "$REPO_URL" && pwd -P)"; fi
cd / || die "Could not change to /"

# ------------------------------------------------------------------ system

OS="$(uname -s)"
case "$OS" in
    Linux) OS=linux ;;
    Darwin) OS=macos ;;
    *) die "Unsupported system: $OS. On Windows use WSL, or run Cloudify with Docker (docker/README.md)." ;;
esac
case "$(uname -m)" in
    x86_64|amd64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    armv7l|armv6l|armhf) ARCH=arm ;;
    i386|i686) ARCH=386 ;;
    *) ARCH="$(uname -m)" ;;
esac

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if have sudo; then SUDO="sudo"; fi
fi
as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"
    elif [ -n "$SUDO" ]; then sudo "$@"
    else return 1
    fi
}

PKG=""
if [ "$OS" = macos ]; then
    have brew && PKG=brew
else
    for pm in apt-get dnf yum pacman zypper apk; do
        if have "$pm"; then PKG="$pm"; break; fi
    done
fi

# shellcheck disable=SC2086  # package lists are intentionally word-split
pkg_install() {  # pkg_install <apt name> <dnf/yum> <pacman> <zypper> <apk> <brew>
    local name=""
    case "$PKG" in
        apt-get) name="$1" ;; dnf|yum) name="$2" ;; pacman) name="$3" ;;
        zypper) name="$4" ;; apk) name="$5" ;; brew) name="$6" ;;
        *) return 1 ;;
    esac
    [ -n "$name" ] && [ "$name" != "-" ] || return 1
    case "$PKG" in
        apt-get)
            if [ -z "${APT_UPDATED:-}" ]; then run as_root apt-get update -qq || true; APT_UPDATED=1; fi
            run as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $name ;;
        dnf|yum) run as_root "$PKG" install -y $name ;;
        # no -Sy: refreshing without upgrading is a partial upgrade, which Arch doesn't support
        pacman) run as_root pacman -S --noconfirm --needed $name ;;
        zypper) run as_root zypper --non-interactive install $name ;;
        apk) run as_root apk add --no-cache $name ;;
        brew) run brew install $name ;;
    esac
}

need_privileges_hint() {  # explain why a system package couldn't be installed
    if [ "$(id -u)" -ne 0 ] && [ -z "$SUDO" ]; then
        warn "Installing system packages needs root. Re-run as root or install sudo, or install these yourself: $*"
    elif [ "$PKG" = pacman ]; then
        warn "pacman could not install: $*. Arch only supports installing on an up-to-date system;"
        warn "run 'sudo pacman -Syu $*' (or the equivalent for your setup), then run this installer again."
    else
        warn "Your package manager ($PKG) could not install: $*. Re-run with --verbose to see why."
    fi
}

# ------------------------------------------------------------------ existing installations
# Every version installs the package "cloudify-cli": 1.x with pipx, or `pip install --user` when
# pipx failed; 2.x with pipx or a virtualenv. Tunnels, their configs and the state file are in
# ~/.cloudflared in every version, so updating replaces the program and moves a few files.

if ! have pipx && [ -x "$HOME/.local/bin/pipx" ]; then export PATH="$HOME/.local/bin:$PATH"; fi

pipx_venv() {  # Cloudify's pipx environment, if there is one
    local d venvs
    venvs="$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || true)"
    for d in "$venvs" "${PIPX_HOME:+$PIPX_HOME/venvs}" "$HOME/.local/share/pipx/venvs" "$HOME/.local/pipx/venvs" \
             "$HOME/Library/Application Support/pipx/venvs"; do
        if [ -n "$d" ] && [ -d "$d/cloudify-cli" ]; then printf '%s\n' "$d/cloudify-cli"; return 0; fi
    done
    return 1
}

dist_version() {  # dist_version DIR: version of cloudify-cli installed in a venv or site-packages DIR
    local d
    for d in "$1"/lib/python3*/site-packages/cloudify_cli-*.dist-info "$1"/cloudify_cli-*.dist-info; do
        if [ -d "$d" ]; then d="${d##*/cloudify_cli-}"; printf '%s\n' "${d%.dist-info}"; return 0; fi
    done
    return 1
}

user_site_installs() {  # copies made with `pip install --user` (the 1.x fallback)
    local d
    for d in "$HOME"/.local/lib/python3*/site-packages/cloudify_cli-*.dist-info \
             "$HOME"/Library/Python/3*/lib/python/site-packages/cloudify_cli-*.dist-info; do
        if [ -d "$d" ]; then printf '%s\n' "$d"; fi
    done
}

EXISTING=""            # how Cloudify is installed now: any of pipx, venv, pip-user
EXISTING_VERSION=""
detect_existing() {
    local v d
    if d="$(pipx_venv)"; then
        EXISTING="$EXISTING pipx"
        EXISTING_VERSION="$(dist_version "$d" || true)"
    fi
    if [ -d "$VENV_DIR" ]; then
        EXISTING="$EXISTING venv"
        v="$(dist_version "$VENV_DIR" || true)"; EXISTING_VERSION="${EXISTING_VERSION:-$v}"
    fi
    while IFS= read -r d; do
        EXISTING="$EXISTING pip-user"
        v="$(dist_version "$(dirname "$d")" || true)"; EXISTING_VERSION="${EXISTING_VERSION:-$v}"
    done < <(user_site_installs)
    EXISTING="${EXISTING# }"
}

remove_user_site_install() {  # remove_user_site_install DIST_INFO: what `pip uninstall` would do
    "$PY" -I - "$1" <<'PY'
import csv, os, shutil, sys
dist = sys.argv[1]
site = os.path.realpath(os.path.dirname(dist))
inside = lambda p: p == site or p.startswith(site + os.sep)
try:
    with open(os.path.join(dist, "RECORD"), newline="") as f:
        rows = [r[0] for r in csv.reader(f) if r]
except OSError:
    rows = []
tops = set()
for rel in rows:
    path = os.path.normpath(os.path.join(site, rel))
    parent = os.path.realpath(os.path.dirname(path))
    if not inside(parent):
        continue  # e.g. the old launcher in ~/.local/bin: replaced by the new one
    target = os.path.join(parent, os.path.basename(path))
    if os.path.islink(target) or os.path.isfile(target):
        os.unlink(target)
    top = rel.split("/")[0]
    if top and top != "..":
        tops.add(top)
# the 1.x dashboard ran npm in its frontend folder: remove what that created
frontend = os.path.join(site, "cli", "frontend")
if "cli" in tops and os.path.isdir(frontend) and not os.path.islink(frontend):
    for name in ("node_modules", ".next"):
        if os.path.isdir(os.path.join(frontend, name)):
            shutil.rmtree(os.path.join(frontend, name), ignore_errors=True)
    for name in ("next-env.d.ts", "tsconfig.tsbuildinfo"):
        if os.path.isfile(os.path.join(frontend, name)):
            os.unlink(os.path.join(frontend, name))
for top in sorted(tops):
    root = os.path.join(site, top)
    if os.path.islink(root) or not os.path.isdir(root):
        continue
    left = [os.path.join(d, n) for d, _, files in os.walk(root) for n in files if not n.endswith(".pyc")]
    if left:
        print("kept " + root)  # holds files that weren't installed by Cloudify
    else:
        shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(dist, ignore_errors=True)
PY
}

remove_user_site_installs() {
    local d kept
    while IFS= read -r d; do
        kept="$(remove_user_site_install "$d")" || { warn "Could not remove the old copy in $(dirname "$d")"; continue; }
        ok "Removed the old copy installed with pip --user ($(dirname "$d"))"
        if [ -n "$kept" ]; then
            printf '%s\n' "$kept" | sed 's/^kept /  left in place (contains other files): /' >&2
        fi
    done < <(user_site_installs)
    # its launcher imported the 1.x code; the new one is a link into Cloudify's own environment
    if [ -f "$BIN_DIR/cloudify" ] && [ ! -L "$BIN_DIR/cloudify" ] && grep -q "cli\.cli" "$BIN_DIR/cloudify" 2>/dev/null; then
        rm -f "$BIN_DIR/cloudify"
    fi
}

dashboard_pids() {  # this user's running `cloudify serve` processes
    local p args
    if [ -r /proc/self/cmdline ]; then
        for p in /proc/[0-9]*; do
            [ -O "$p" ] || continue
            args="$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null || true)"
            case " $args" in
                *"/cloudify serve"*|*" cloudify serve"*) printf '%s\n' "${p#/proc/}" ;;
            esac
        done
    else
        ps -ww -U "$(id -u)" -o pid=,command= 2>/dev/null | awk '/(^|[ \/])cloudify serve/ {print $1}' || true
    fi
}

process_cwd() {
    if [ -e "/proc/$1/cwd" ]; then readlink "/proc/$1/cwd" 2>/dev/null || true
    elif have lsof; then lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true
    fi
}

backup_settings() {  # small safety copy of Cloudify's settings (not the tunnel credentials)
    local dir="$HOME/.cloudflared" backups out f all n i
    local files=()
    [ -d "$dir" ] || return 0
    for f in tunnel_manager_state.json cloudify/users.json cloudify/accounts.json; do
        if [ -f "$dir/$f" ]; then files+=("$f"); fi
    done
    for f in "$dir"/config-*.yml; do
        if [ -f "$f" ]; then files+=("${f#"$dir"/}"); fi
    done
    [ ${#files[@]} -gt 0 ] || return 0
    backups="$dir/cloudify/backups"
    if ! { mkdir -p "$backups" && chmod 700 "$dir/cloudify" "$backups"; } 2>/dev/null; then
        warn "Could not create $backups: settings not backed up"
        return 0
    fi
    out="$backups/before-update-$(date +%Y%m%d-%H%M%S).tar.gz"
    if (umask 077 && tar -C "$dir" -czf "$out" "${files[@]}"); then
        ok "Settings backed up to $(tildify "$out")"
    else
        warn "Could not back up the settings to $out"
    fi
    all=("$backups"/before-update-*.tar.gz)          # oldest first; keep the newest five
    n=$(( ${#all[@]} - 5 ))
    for ((i = 0; i < n; i++)); do rm -f "${all[$i]}"; done
}

migrate_users() {  # 1.x kept dashboard accounts in users.json in the folder `cloudify serve` ran in
    local target="$HOME/.cloudflared/cloudify/users.json"
    [ -e "$target" ] && return 0
    "$PY" -I - "$target" "$@" <<'PY' || true
import json, os, stat, sys
target, candidates = sys.argv[1], sys.argv[2:]
for path in candidates:
    try:
        st = os.lstat(path)
    except OSError:
        continue
    # only a regular file of this user that nobody else could have written (group write is fine
    # for the user's own private group, which Ubuntu's umask 002 produces)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o002:
        continue
    if st.st_mode & 0o020:
        try:
            import grp, pwd
            group, me = grp.getgrgid(st.st_gid), pwd.getpwuid(st.st_uid)
            if not (group.gr_name == me.pw_name and all(m == me.pw_name for m in group.gr_mem)):
                continue
        except (ImportError, KeyError):
            continue
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (st.st_dev, st.st_ino):
            os.close(fd)
            continue  # replaced between the check and the read
        with os.fdopen(fd) as f:
            users = json.load(f)
    except (OSError, ValueError):
        continue
    if not (isinstance(users, dict) and users and all(
            isinstance(u, dict) and str(u.get("hashed_password", "")).startswith("$2") for u in users.values())):
        continue
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(users, f, indent=4)
    print(path)
    break
PY
}

tunnel_count() {
    "$PY" -I -c 'import json, os, sys
try:
    print(len(json.load(open(os.path.expanduser("~/.cloudflared/tunnel_manager_state.json"))).get("tunnels") or {}))
except Exception:
    print(0)' 2>/dev/null || echo 0
}

# ------------------------------------------------------------------ uninstall

if $UNINSTALL; then
    step "Removing Cloudify"
    if have pipx && pipx list --short 2>/dev/null | grep -q '^cloudify-cli '; then
        run pipx uninstall cloudify-cli && ok "Removed (pipx)"
    fi
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        ok "Removed $VENV_DIR"
    fi
    if [ -L "$BIN_DIR/cloudify" ]; then rm -f "$BIN_DIR/cloudify"; fi
    if [ -n "$(user_site_installs)" ]; then
        if find_python || have python3; then remove_user_site_installs
        else warn "An old copy installed with pip --user is left in ~/.local (python3 is needed to remove it)"
        fi
    fi
    if $PURGE; then
        rm -rf "$HOME/.cloudflared/cloudify"
        ok "Deleted ~/.cloudflared/cloudify (accounts, users, logs)"
    fi
    info "Tunnels, their configs and cloudflared itself were left in place."
    info "Stop or delete tunnels first with the cloudify CLI if you no longer need them."
    exit 0
fi

printf '%sCloudify installer%s  %s(%s/%s, package manager: %s)%s\n' "$BOLD" "$RESET" "$DIM" "$OS" "$ARCH" "${PKG:-none}" "$RESET"

# ------------------------------------------------------------------ 0. current installation

detect_existing
DASHBOARDS="$(dashboard_pids | tr '\n' ' ')"
DASHBOARDS="${DASHBOARDS% }"
if [ -n "$EXISTING" ] || $UPDATE; then
    step "Current installation"
    if [ -n "$EXISTING" ]; then
        ok "Cloudify ${EXISTING_VERSION:-(unknown version)} (installed with: $EXISTING)"
        backup_settings
    else
        info "Cloudify isn't installed yet: installing it."
    fi
fi

# ------------------------------------------------------------------ 1. prerequisites

step "Checking prerequisites"

if [ "$OS" = macos ] && [ -z "$PKG" ]; then
    die "Homebrew is required on macOS. Install it from https://brew.sh and run this again."
fi

if ! find_python; then
    info "Installing Python 3..."
    pkg_install "python3 python3-venv" python3 python python3 "python3" python@3.12 \
        || { need_privileges_hint "$([ "$PKG" = pacman ] && echo "python python-pipx git" || echo python3)"; \
             die "Python 3.8 or newer is required"; }
    if ! find_python; then
        # the distribution's python3 is too old (RHEL/Rocky/Alma 8, Ubuntu 18.04): add a newer one next to it
        info "Installing a newer Python next to $(python3 --version 2>&1 || echo python3)..."
        pkg_install "python3.8 python3.8-venv" python3.11 - - - - || true
        find_python || die "Python 3.8 or newer is required (found: $(python3 --version 2>&1 || echo none)). Install python3.8 or newer and run this again."
    fi
fi
ok "$("$PY" --version) ($PY)"

have curl || pkg_install curl curl curl curl curl curl || true
have curl || have wget || die "curl or wget is required"

if [ -z "$LOCAL_SRC" ] && ! have git; then
    info "Installing git..."
    pkg_install git git git git git git || { need_privileges_hint git; die "git is required (or use --local DIR)"; }
fi

venv_ok() { "$PY" -I -c 'import venv, ensurepip' >/dev/null 2>&1; }
if ! have pipx; then
    info "Installing pipx..."
    pkg_install pipx pipx python-pipx python3-pipx pipx pipx \
        || run "$PY" -I -m pip install --user --quiet pipx \
        || true
    if ! have pipx && [ -x "$HOME/.local/bin/pipx" ]; then export PATH="$HOME/.local/bin:$PATH"; fi
fi
if have pipx; then
    INSTALLER=pipx
    ok "pipx $(pipx --version 2>/dev/null)"
else
    venv_ok || pkg_install python3-venv - - - - - || true
    venv_ok || die "Neither pipx nor Python's venv module is available. Install pipx (https://pipx.pypa.io) and run this again."
    INSTALLER=venv
    ok "Python venv (pipx not available)"
fi

# ------------------------------------------------------------------ 2. cloudflared

cloudflared_version() { cloudflared --version 2>/dev/null | awk '{print $3}'; }
published_sha256() {  # published_sha256 FILE: checksum from the release notes on stdin ("FILE: <sha256>")
    "$PY" -I -c 'import json, re, sys
try:
    body = json.load(sys.stdin).get("body") or ""
except ValueError:
    body = ""
m = re.search(r"^" + re.escape(sys.argv[1]) + r":\s*([0-9a-f]{64})\s*$", body, re.M)
print(m.group(1) if m else "")' "$1" 2>/dev/null || true
}
file_sha256() { "$PY" -I -c 'import hashlib, sys
with open(sys.argv[1], "rb") as f:
    print(hashlib.sha256(f.read()).hexdigest())' "$1"; }

install_cloudflared() {
    step "cloudflared"
    local latest current target file tmp url release expected
    release="$(fetch https://api.github.com/repos/cloudflare/cloudflared/releases/latest 2>/dev/null || true)"
    latest="$(printf '%s' "$release" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
    if have cloudflared; then
        current="$(cloudflared_version)"
        if [ -n "$latest" ] && [ "$current" = "$latest" ]; then
            ok "cloudflared $current (latest)"
            return
        fi
    fi

    if [ "$OS" = macos ]; then
        if brew list cloudflared >/dev/null 2>&1; then run brew upgrade cloudflared || true
        else
            # 1.x installers put a plain binary in /usr/local/bin, which blocks Homebrew's link there
            run brew install cloudflared || run brew link --overwrite cloudflared \
                || die "Homebrew could not install cloudflared (re-run with --verbose to see why)"
        fi
        hash -r 2>/dev/null || true
        ok "cloudflared $(cloudflared_version) (Homebrew)"
        if [ -x /usr/local/bin/cloudflared ] && [ ! -L /usr/local/bin/cloudflared ] \
           && [ "$(brew --prefix)" != /usr/local ]; then
            warn "An older cloudflared from a previous installer is still in /usr/local/bin."
            warn "Remove it (sudo rm /usr/local/bin/cloudflared) so every tunnel uses Homebrew's copy."
        fi
        return
    fi

    target="/usr/local/bin/cloudflared"
    if have cloudflared; then
        target="$(command -v cloudflared)"
        if { have dpkg && dpkg -S "$target" >/dev/null 2>&1; } || { have rpm && rpm -qf "$target" >/dev/null 2>&1; }; then
            warn "cloudflared $current is managed by your package manager; update it with $PKG (latest: ${latest:-unknown})"
            return
        fi
        info "Updating cloudflared ${current:-?} -> ${latest:-latest}"
    else
        info "Installing cloudflared ${latest:-latest}"
    fi

    case "$ARCH" in amd64|arm64|arm|386) file="cloudflared-linux-$ARCH" ;; *) die "No cloudflared build for $ARCH" ;; esac
    tmp="$(mktemp -d)"
    if [ -n "$latest" ]; then
        url="https://github.com/cloudflare/cloudflared/releases/download/$latest/$file"
    else
        url="https://github.com/cloudflare/cloudflared/releases/latest/download/$file"
    fi
    fetch "$url" "$tmp/cloudflared" || { rm -rf "$tmp"; die "Could not download cloudflared ($url)"; }
    # Check it against the SHA-256 that Cloudflare publishes in the release notes, before running it
    expected="$(printf '%s' "$release" | published_sha256 "$file")"
    if [ -n "$latest" ] && [ -n "$expected" ]; then
        if [ "$(file_sha256 "$tmp/cloudflared")" != "$expected" ]; then
            rm -rf "$tmp"
            die "The downloaded cloudflared doesn't match the checksum Cloudflare published for $latest; not installing it"
        fi
        ok "Checksum verified (SHA-256 published for $latest)"
    else
        warn "No published checksum found for $file; installing without checksum verification"
    fi
    chmod 0755 "$tmp/cloudflared"
    "$tmp/cloudflared" --version >/dev/null 2>&1 || { rm -rf "$tmp"; die "The downloaded cloudflared doesn't run on this system"; }

    if [ -w "$(dirname "$target")" ]; then
        install -m 0755 "$tmp/cloudflared" "$target"
    elif ! as_root install -m 0755 "$tmp/cloudflared" "$target" 2>/dev/null; then
        target="$BIN_DIR/cloudflared"      # no root: install for this user only
        mkdir -p "$BIN_DIR"
        install -m 0755 "$tmp/cloudflared" "$target"
        warn "No root access: installed cloudflared to $target (only for this user)"
    fi
    rm -rf "$tmp"
    hash -r 2>/dev/null || true
    ok "cloudflared $("$target" --version | awk '{print $3}') at $target"
}

if $SKIP_CLOUDFLARED; then
    step "cloudflared"; info "Skipped (--skip-cloudflared)"
else
    install_cloudflared
fi

# ------------------------------------------------------------------ 3. Cloudify

step "Installing Cloudify"
WORK=""
cleanup() { if [ -n "$WORK" ]; then rm -rf "$WORK"; fi; }
trap cleanup EXIT
# The build folder is in the user's own cache, not the shared /tmp: pipx records the path it
# installed from, and nobody else must be able to recreate it later.
work_dir() {
    local cache="${XDG_CACHE_HOME:-$HOME/.cache}"
    mkdir -p "$cache" && chmod 700 "$cache" 2>/dev/null || true
    mktemp -d "$cache/cloudify-install.XXXXXX"
}

if [ -n "$LOCAL_SRC" ]; then
    [ -f "$LOCAL_SRC/setup.py" ] || die "$LOCAL_SRC doesn't look like a Cloudify checkout (no setup.py)"
    info "From local folder $(cd "$LOCAL_SRC" && pwd)"
    # Build from a copy: pip writes build files into the source folder otherwise
    # (and fails if it is read-only)
    WORK="$(work_dir)"
    mkdir -p "$WORK/src"
    tar -C "$LOCAL_SRC" --exclude=.git --exclude=build --exclude='*.egg-info' --exclude=node_modules \
        --exclude=__pycache__ -cf - . | tar -C "$WORK/src" -xf -
    SRC="$WORK/src"
else
    WORK="$(work_dir)"
    info "From $REPO_URL ($BRANCH)"
    run git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$WORK/src" \
        || die "Could not download $REPO_URL (branch $BRANCH)"
    SRC="$WORK/src"
fi

case " $EXISTING " in *" pip-user "*) remove_user_site_installs ;; esac

if [ "$INSTALLER" = pipx ]; then
    # pipx shares one pip among everything it installs, made for the Python pipx runs on, and new pip
    # versions drop old Pythons (pip 26.1 needs 3.10). The Python found above can be older than pipx's:
    # on a Mac, Apple's 3.9 comes before Homebrew's Python when /usr/bin is first in PATH. So pipx's
    # own Python comes first; the one found above is for a pipx running on Python older than 3.8.
    run pipx install --force "$SRC" \
        || run pipx install --force --python "$PY" "$SRC" \
        || die "pipx could not install Cloudify (re-run with --verbose to see why)"
    CLOUDIFY_BIN="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "$BIN_DIR")/cloudify"
else
    mkdir -p "$(dirname "$VENV_DIR")" "$BIN_DIR"
    rm -rf "$VENV_DIR"
    run "$PY" -I -m venv "$VENV_DIR" || die "Could not create $VENV_DIR"
    run "$VENV_DIR/bin/pip" install --upgrade --quiet pip
    run "$VENV_DIR/bin/pip" install --quiet "$SRC" || die "pip could not install Cloudify (re-run with --verbose)"
    ln -sf "$VENV_DIR/bin/cloudify" "$BIN_DIR/cloudify"
    CLOUDIFY_BIN="$BIN_DIR/cloudify"
fi
if ! VERSION_OUT="$("$CLOUDIFY_BIN" --version 2>&1)"; then
    printf '%s\n' "$VERSION_OUT" | tail -8 | sed 's/^/    /' >&2
    die "Cloudify was installed but doesn't start ($CLOUDIFY_BIN). Please report the error above."
fi
VERSION="$(printf '%s' "$VERSION_OUT" | awk '{print $NF}')"
ok "Cloudify $VERSION ($INSTALLER)"
if [ "$INSTALLER" = pipx ] && [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"      # an earlier install without pipx: replaced by the pipx one
fi

# 1.x dashboards kept their accounts in users.json in their working folder
USER_CANDIDATES=("$HOME/users.json" "$ORIG_PWD/users.json")
for pid in $DASHBOARDS; do
    cwd="$(process_cwd "$pid")"
    if [ -n "$cwd" ]; then USER_CANDIDATES+=("$cwd/users.json"); fi
done
MIGRATED_USERS="$(migrate_users "${USER_CANDIDATES[@]}")"
if [ -n "$MIGRATED_USERS" ]; then
    ok "Dashboard accounts moved from $(tildify "$MIGRATED_USERS") to ~/.cloudflared/cloudify/users.json"
fi

# ------------------------------------------------------------------ 4. PATH

step "Finishing"
BIN_PARENT="$(dirname "$CLOUDIFY_BIN")"
case ":$ORIGINAL_PATH:" in
    *":$BIN_PARENT:"*) ok "cloudify is on your PATH" ;;
    *)
      case "$BIN_PARENT" in
        *[!A-Za-z0-9/._+@%,:~-]*)
        warn "Not adding $BIN_PARENT to your shell's PATH automatically (unusual characters); add it yourself"
        ;;
        *)
        added=""
        rcs=("$HOME/.profile")
        # bash reads ~/.bashrc in terminals, but login shells (macOS Terminal, SSH) read ~/.bash_profile
        # instead of ~/.profile when it exists; zsh (macOS default) reads only ~/.zshrc, which a new Mac
        # account doesn't have yet
        if [ -f "$HOME/.bashrc" ]; then rcs+=("$HOME/.bashrc"); fi
        if [ -f "$HOME/.bash_profile" ]; then rcs+=("$HOME/.bash_profile"); fi
        case "${SHELL:-}" in
            */zsh) rcs+=("$HOME/.zshrc") ;;
            *) if [ -f "$HOME/.zshrc" ]; then rcs+=("$HOME/.zshrc"); fi ;;
        esac
        for rc in "${rcs[@]}"; do
            if ! grep -qs "$BIN_PARENT" "$rc"; then
                # shellcheck disable=SC2016  # $PATH must be written literally
                printf '\n# Added by the Cloudify installer\nexport PATH="%s:$PATH"\n' "$BIN_PARENT" >> "$rc"
                added="$added $(tildify "$rc")"
            fi
        done
        if [ -d "$HOME/.config/fish" ] && ! grep -qs "$BIN_PARENT" "$HOME/.config/fish/config.fish"; then
            mkdir -p "$HOME/.config/fish"
            printf '\n# Added by the Cloudify installer\nfish_add_path %s\n' "$BIN_PARENT" >> "$HOME/.config/fish/config.fish"
            added="$added ~/.config/fish/config.fish"
        fi
        ok "Added $BIN_PARENT to PATH in:${added:- (already configured)}"
        warn "Open a new terminal (or run: export PATH=\"$BIN_PARENT:\$PATH\") to use cloudify"
        ;;
      esac
      ;;
esac

if ! have ssh; then
    info "${DIM}Optional: install the OpenSSH client (ssh) to use 'cloudify -ssh remote'.${RESET}"
fi

if [ -n "$EXISTING" ]; then
    TUNNELS="$(tunnel_count)"
    printf '\n%s%sCloudify updated: %s -> %s%s\n' "$GREEN" "$BOLD" "${EXISTING_VERSION:-?}" "$VERSION" "$RESET"
    info "Kept: $TUNNELS tunnel(s) with their routes and auto-start, Cloudflare logins, dashboard accounts."
    info "Running tunnels were not interrupted."
    if [ -n "$DASHBOARDS" ]; then
        warn "The dashboard is still running the previous version (process $DASHBOARDS)."
        warn "Restart it: stop it (Ctrl+C in its terminal, or: kill $DASHBOARDS), then run: cloudify serve"
    fi
else
    printf '\n%s%sCloudify %s is installed.%s\n' "$GREEN" "$BOLD" "$VERSION" "$RESET"
fi
if have docker && docker ps --format '{{.Image}} {{.Names}}' 2>/dev/null | grep -qi cloudify; then
    info "${DIM}A Cloudify Docker container is running: containers are updated separately (docs/docker.md).${RESET}"
fi

cat <<EOF

  cloudify setup --domain example.com   connect your Cloudflare domain
  cloudify serve                        dashboard on port 8765, open to the network
  cloudify serve --secure               dashboard on this machine only (http://localhost:8765)
  cloudify -p 3000                      temporary public URL for localhost:3000
  cloudify -ssh host                    share this machine over SSH

  Update: install.sh --update         Remove: install.sh --uninstall
EOF
