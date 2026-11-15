# Install on Linux

One script installs everything Cloudify needs on any common distribution. It asks no questions, can be run
again at any time to repair or update, and never needs Node.js or a build toolchain.

```bash
curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh | bash
```

Prefer to read it first? Download, inspect, run:

```bash
curl -fsSLO https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh
less install.sh
bash install.sh
```

Open a new terminal afterwards if `cloudify` is not found yet (the installer adds `~/.local/bin` to your
shell's PATH when it was missing). Then continue with [Getting started](getting-started.md).

## What the installer does

1. **Checks the current installation.** If Cloudify (any version, including 1.x) is installed, it backs up
   the settings and replaces the program; see [Updating](updating.md).
2. **Installs missing prerequisites** with your package manager: Python 3.8+, pipx (or Python's `venv`
   module as a fallback), git and curl. `sudo` is used only for this and for `/usr/local/bin`.
3. **Installs cloudflared** for your CPU (amd64, arm64, armv7, i386) from Cloudflare's GitHub releases into
   `/usr/local/bin`, or updates it when an older version is found. The download is checked against the
   SHA-256 checksum Cloudflare publishes in the release notes before it is run; a mismatch stops the
   installer. A cloudflared installed by your package manager (`.deb`/`.rpm`) is left to the package manager.
4. **Installs Cloudify** in its own isolated environment with pipx (in pipx's folder, e.g.
   `~/.local/share/pipx/venvs/cloudify-cli`), or in a virtualenv at `~/.local/share/cloudify/venv` when pipx
   isn't available. Nothing is installed into the system Python.
5. **Puts `cloudify` on your PATH** (`~/.local/bin`) and checks that it starts.

Your tunnels and settings live in `~/.cloudflared` and are never touched by installing, updating or
uninstalling ([Files and data](files-and-data.md)).

## Options

Pass options after `bash -s --` when piping, or directly to `bash install.sh`:

```bash
curl -fsSL .../install.sh | bash -s -- --update
bash install.sh --branch dev --verbose
```

| Option | Effect |
| --- | --- |
| `--update` | Update an existing installation, including Cloudify 1.x ([details](updating.md)) |
| `--branch NAME` | Install a branch or tag instead of `main` |
| `--repo URL` | Install from another Git repository (a fork, or a local path) |
| `--local DIR` | Install from a folder you already have (`bash install.sh --local .`); no git needed |
| `--skip-cloudflared` | Don't install or update cloudflared |
| `--uninstall` | Remove Cloudify; tunnels, settings and cloudflared stay |
| `--uninstall --purge` | Also delete Cloudify's own data in `~/.cloudflared/cloudify` (users, connections) |
| `--verbose`, `-v` | Show the output of every command |
| `-h`, `--help` | Show the options |

## Distribution notes

| Distribution | Notes |
| --- | --- |
| **Ubuntu 20.04+, Debian 11+, Mint, Pop!_OS** | Works out of the box. On Ubuntu 23.04+/Debian 12+ `pip install` into the system is blocked by design (PEP 668); the installer uses pipx, so this doesn't matter. |
| **Raspberry Pi OS** | Same as Debian. 64-bit images get the arm64 cloudflared, 32-bit images the armv7 one. |
| **Fedora, RHEL 9, Rocky/Alma 9, CentOS Stream** | Uses `dnf`. Python 3.9+ is included. |
| **RHEL/Rocky/Alma 8, older Ubuntu/Debian** | The default `python3` may be too old (3.6/3.7). Install a newer Python next to it, e.g. `sudo dnf install -y python3.11` (RHEL 8) or `sudo apt install python3.9 python3.9-venv` (Ubuntu 18.04); the installer finds `python3.8` ... `python3.14` by itself and uses the newest one. |
| **Arch, Manjaro, EndeavourOS** | Arch supports installing packages only on an up-to-date system. The installer never runs a partial upgrade (`pacman -Sy`); if a package can't be installed it stops and tells you to run `sudo pacman -Syu python python-pipx git` first. |
| **openSUSE Leap/Tumbleweed** | Uses `zypper`. |
| **Alpine** | Install bash first: `apk add bash curl`. Without systemd, auto-start uses cron `@reboot`; make sure `crond` runs at boot (`rc-update add crond`). |
| **NixOS, Gentoo, Void, Clear Linux** | No supported package manager: install Python 3.8+, pipx and git yourself, then run the installer (it skips what is present). |
| **WSL 2 (Windows)** | Same as the distribution you run in WSL. See [Windows](install-windows.md) for auto-start. |

### Without root or sudo

The installer still works if Python 3.8+, git and curl are already there: Cloudify goes to `~/.local`,
and cloudflared to `~/.local/bin/cloudflared` (for your user only). If something is missing it prints the
exact package names to ask an administrator for.

## Manual installation

If you prefer to do it by hand:

```bash
# 1. cloudflared: Cloudflare's package repository (Debian/Ubuntu example)
#    https://pkg.cloudflare.com/index.html, or a release binary:
curl -fsSL -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo install -m 0755 cloudflared /usr/local/bin/cloudflared

# 2. Cloudify with pipx
pipx install git+https://github.com/yash7158/Cloudify_v2.git

# or in a virtualenv
python3 -m venv ~/.local/share/cloudify/venv
~/.local/share/cloudify/venv/bin/pip install git+https://github.com/yash7158/Cloudify_v2.git
ln -s ~/.local/share/cloudify/venv/bin/cloudify ~/.local/bin/cloudify
```

Check: `cloudify --version` and `cloudflared --version`.

## Auto-start on Linux

With systemd (almost every desktop and server distribution), each tunnel with auto-start gets a **user**
service; no root is needed:

```bash
systemctl --user status cloudflared-tunnel-web
journalctl --user -u cloudflared-tunnel-web -f      # live logs
systemctl --user restart cloudflared-tunnel-web
```

Cloudify also runs `loginctl enable-linger $USER`, so user services start at boot even when nobody logs
in. If your system doesn't allow that (some hardened servers), the tunnel starts when you log in; an
administrator can run `sudo loginctl enable-linger <user>`. Check with `loginctl show-user $USER -p Linger`.

Without systemd (Alpine, many containers), Cloudify writes `~/.local/bin/start-tunnel-<name>.sh` and an
`@reboot` line in your crontab (`crontab -l`). The tunnel log is `~/.cloudflared/logs/<name>.log`.

## Run the dashboard as a service

Auto-start covers tunnels, not the dashboard. To keep `cloudify serve` running, create a user service:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/cloudify-dashboard.service <<'EOF'
[Unit]
Description=Cloudify dashboard
After=network-online.target

[Service]
ExecStart=%h/.local/bin/cloudify serve --host 127.0.0.1 --serve-port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now cloudify-dashboard
loginctl enable-linger "$USER"        # start at boot without logging in
```

`--host 127.0.0.1` (the default) keeps the dashboard private to this machine. To reach it from your laptop use
an SSH tunnel (`ssh -L 8765:localhost:8765 user@server`, then open `http://localhost:8765`), or serve HTTPS
on the network with `--host 0.0.0.0 --ssl-certfile ... --ssl-keyfile ...`. Read [Security](security.md)
first.

## Firewall

Tunnels make outbound connections only (to Cloudflare on port 7844, UDP for QUIC and TCP as fallback), so
no inbound port has to be opened. Only the dashboard listens on a port (8765 by default).

## Next steps

* [Getting started](getting-started.md)
* [Updating and uninstalling](updating.md)
* [Troubleshooting](troubleshooting.md)
