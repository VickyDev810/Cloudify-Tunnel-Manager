# Windows

There are three ways to run Cloudify on Windows 10 or 11. **WSL 2** or **Docker Desktop** are recommended:
they use the same tested Linux code paths. A native installation works but is experimental (it is not
covered by the automated tests).

| | WSL 2 | Docker Desktop | Native (experimental) |
| --- | --- | --- | --- |
| Installer | `install.sh` inside Ubuntu | `docker compose` | `pip`/`pipx` by hand |
| Tunnels start | when WSL runs | when Docker Desktop starts | at Windows sign-in (Task Scheduler) |
| Reach services on Windows | `localhost` (WSL forwards it) or the Windows IP | `host.docker.internal` | `localhost` |
| Best for | development machines | always-on PCs and home servers | trying it out |

## Option 1: WSL 2 (recommended for development)

1. Open PowerShell as administrator and install Ubuntu (restart when asked):

   ```powershell
   wsl --install -d Ubuntu
   ```

2. Open **Ubuntu** from the Start menu, create your Linux user, then install Cloudify as on any Linux:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh | bash
   ```

3. Continue with [Getting started](getting-started.md). The dashboard (`cloudify serve`) is reachable from
   Windows at `http://localhost:8765`.

Notes:

* Current Ubuntu images for WSL run systemd, so auto-start uses user services like on Linux. Check with
  `systemctl --user status`; if it fails, add `[boot]` / `systemd=true` to `/etc/wsl.conf` and run
  `wsl --shutdown` in PowerShell.
* WSL runs while it is in use; tunnels stop when WSL shuts down. For tunnels that must run whenever the
  PC is on, use Docker Desktop (option 2).
* Services running on Windows itself are reachable from WSL at the Windows host's IP
  (`ip route show default | awk '{print $3}'`), or at `localhost` when WSL's *mirrored* networking mode is on.

## Option 2: Docker Desktop (recommended for always-on use)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and enable
   *Settings → General → Start Docker Desktop when you sign in*.
2. In PowerShell (with [Git for Windows](https://git-scm.com/download/win) installed):

   ```powershell
   git clone https://github.com/yash7158/Cloudify_v2.git
   cd Cloudify_v2
   docker compose -f docker/docker-compose.yml up -d --build
   ```

3. Open `http://localhost:8765`. For services running on Windows use the service host
   `host.docker.internal` in your routes.

Everything else is in the [Docker guide](docker.md).

## Option 3: native Windows (experimental)

1. Install Python 3.8+ and cloudflared (PowerShell):

   ```powershell
   winget install -e --id Python.Python.3.12
   winget install -e --id Cloudflare.cloudflared
   ```

   Close and reopen PowerShell so both are on the PATH (`py --version`, `cloudflared --version`).

2. Install Cloudify with pipx:

   ```powershell
   py -m pip install --user pipx
   py -m pipx ensurepath
   # reopen PowerShell, then:
   pipx install https://github.com/yash7158/Cloudify_v2/archive/refs/heads/main.zip
   cloudify --version
   ```

3. Use it as described in [Getting started](getting-started.md).

How it differs from Linux and macOS:

* **Auto-start** creates a scheduled task `CloudflaredTunnel-<name>` that runs at sign-in
  (`schtasks /query /tn CloudflaredTunnel-web`), using a batch file in `%USERPROFILE%\.cloudflared`.
* **Data** lives in `%USERPROFILE%\.cloudflared` (same layout as `~/.cloudflared`, see
  [Files and data](files-and-data.md)).
* **SSH sharing:** `cloudify -ssh remote` uses the OpenSSH client built into Windows 10/11.
  `cloudify -ssh host` works, but the remote gets a shell without a terminal (commands and SFTP work,
  full-screen programs such as `vim` don't).
* **Updating:** `pipx install --force https://github.com/yash7158/Cloudify_v2/archive/refs/heads/main.zip`;
  **removing:** `pipx uninstall cloudify-cli`.

Earlier versions of the README mentioned a Windows `.exe` in the releases section. This version doesn't ship
one; use one of the options above.
