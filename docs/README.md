# Cloudify documentation

Cloudify manages [Cloudflare Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
from a command line (`cloudify`) and a web dashboard (`cloudify serve`). It publishes services that run on
your machine (a website on `localhost:3000`, a container, an SSH shell) on the internet without opening
ports in your firewall or router.

## Which guide do I need?

| I want to... | Read |
| --- | --- |
| Understand the concepts and set up my first tunnel | [Getting started](getting-started.md) |
| Install on Linux (Ubuntu, Debian, Fedora, RHEL, Arch, openSUSE, Alpine, Raspberry Pi) | [Install on Linux](install-linux.md) |
| Install on macOS (Intel or Apple Silicon) | [Install on macOS](install-macos.md) |
| Run it in Docker or Docker Compose (any OS, NAS, Raspberry Pi) | [Docker](docker.md) |
| Use it on Windows | [Windows](install-windows.md) |
| Update an existing installation (including Cloudify 1.x) | [Updating and uninstalling](updating.md) |
| Use the web dashboard | [Dashboard](dashboard.md) |
| Look up a command | [CLI reference](cli-reference.md) |
| Share a machine's shell temporarily over SSH | [SSH sharing](ssh.md) |
| Know where my data is, back it up or move it | [Files, data and backups](files-and-data.md) |
| Fix a problem | [Troubleshooting](troubleshooting.md) |
| Run it safely (dashboard exposure, users, secrets) | [Security](security.md) |
| See what is reachable through Cloudflare, stop it, or remove everything | [Safety check](security.md#safety-check) |

## Supported platforms

| Platform | How | Start at boot |
| --- | --- | --- |
| Ubuntu, Debian, Linux Mint, Pop!_OS, Raspberry Pi OS | `install.sh` (apt) | systemd user services (+ linger) |
| Fedora, RHEL, Rocky, AlmaLinux, CentOS Stream | `install.sh` (dnf/yum) | systemd user services (+ linger) |
| Arch, Manjaro, EndeavourOS | `install.sh` (pacman) | systemd user services (+ linger) |
| openSUSE Leap/Tumbleweed | `install.sh` (zypper) | systemd user services (+ linger) |
| Alpine and other systems without systemd | `install.sh` (apk) | cron `@reboot` |
| macOS with Homebrew (Intel and Apple Silicon) | `install.sh` (Homebrew) | LaunchAgents (at login) |
| Any OS with Docker (Linux servers, NAS, Docker Desktop) | `docker/` | container restart policy |
| Windows 10/11 | WSL 2 + `install.sh`, or Docker Desktop | see [Windows](install-windows.md) |

Architectures: x86_64 (amd64), arm64 (aarch64, Apple Silicon, Raspberry Pi 4/5 64-bit), armv7 (32-bit
Raspberry Pi OS) and i386; cloudflared is installed for the matching architecture.

## Requirements in one place

* **Python 3.8 or newer** (the installer installs it when missing; Docker images include it).
* **cloudflared** (installed and kept up to date by the installer and the Docker image).
* For your own hostnames (`app.example.com`): a **domain on Cloudflare** (free plan is enough) and a
  Cloudflare account. Temporary tunnels (`cloudify -p 3000`) and SSH sharing need neither.
* For automatic deletion of DNS records: a Cloudflare **API token** with `Zone · DNS · Edit`
  (optional, see [Getting started](getting-started.md#dns-records-and-the-api-token)).
* For `cloudify -ssh remote`: the OpenSSH client (`ssh`), present on Linux, macOS and Windows 10+.
