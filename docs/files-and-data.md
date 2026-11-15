# Files, data and backups

Everything Cloudify knows is stored in your home folder, in `~/.cloudflared` (cloudflared's own folder). The
program itself can be removed, updated or reinstalled without touching it. In Docker this folder is the
`/root/.cloudflared` volume. On Windows it is `%USERPROFILE%\.cloudflared`.

## What is where

```text
~/.cloudflared/
├── cert.pem                       first Cloudflare login (cloudflared's default certificate)       SECRET
├── <tunnel-id>.json               credentials of each tunnel                                       SECRET
├── config-<name>.yml              each tunnel's configuration: its routes (ingress rules)
├── tunnel_manager_state.json      Cloudify's list of tunnels, routes, auto-start flags, replicas
├── logs/<name>.log                log of tunnels started directly (not by systemd/launchd)
└── cloudify/
    ├── users.json                 dashboard accounts (bcrypt password hashes)                      600
    ├── accounts.json              connected domains; DNS-cleanup API tokens                        SECRET, 600
    ├── certs/<id>.pem             certificates of additionally connected domains                   SECRET, 600
    ├── status.json, login.log     progress of the last Cloudflare login
    └── backups/                   settings backups made by the installer before each update        600
```

Outside `~/.cloudflared`:

| What | Where |
| --- | --- |
| Program (pipx) | `~/.local/share/pipx/venvs/cloudify-cli/` (or `~/.local/pipx/venvs/...` with older pipx), launcher `~/.local/bin/cloudify` |
| Program (virtualenv fallback) | `~/.local/share/cloudify/venv/`, launcher `~/.local/bin/cloudify` |
| cloudflared | `/usr/local/bin/cloudflared`, Homebrew, your package manager, or `~/.local/bin/cloudflared` |
| Auto-start, systemd | `~/.config/systemd/user/cloudflared-tunnel-<name>.service` |
| Auto-start, cron | `~/.local/bin/start-tunnel-<name>.sh` and a line in `crontab -l` |
| Auto-start, macOS | `~/Library/LaunchAgents/com.user.cloudflare.tunnel.<name>.plist`, logs in `~/Library/Logs/` |
| Auto-start, Windows | scheduled task `CloudflaredTunnel-<name>`, batch file `%USERPROFILE%\.cloudflared\tunnel-<name>.bat` |

## What is secret

* **`<tunnel-id>.json`**: whoever has it can run your tunnel and receive its traffic.
* **`cert.pem` and `cloudify/certs/*.pem`**: allow creating and deleting tunnels and DNS records for that domain's
  Cloudflare account.
* **`cloudify/accounts.json`**: contains DNS-cleanup API tokens.
* **Replica files** you export contain tunnel credentials, encrypted with your passphrase.

Keep the folder private: `chmod 700 ~/.cloudflared`. Never commit it to Git or include it in an image.

## Backup

```bash
tar -czf cloudify-backup-$(date +%F).tar.gz -C ~ .cloudflared         # contains secrets: store it safely
```

Docker: see [Docker → Backup and restore](docker.md#backup-and-restore).

The installer additionally keeps small backups of the settings (not the secrets) in
`~/.cloudflared/cloudify/backups/` before every update.

## Restore, or move to a new machine

1. Install Cloudify on the new machine ([Linux](install-linux.md), [macOS](install-macos.md), [Docker](docker.md)).
2. **Stop the tunnels on the old machine** (`cloudify tunnel stop <name>` and disable auto-start), unless you
   want both machines to serve the tunnel: two machines with the same credentials act as replicas.
3. Restore the folder: `tar -xzf cloudify-backup-2026-11-15.tar.gz -C ~`
4. Recreate auto-start entries (they live outside `~/.cloudflared`), then check:

   ```bash
   cloudify tunnel resume            # starts every tunnel marked for auto-start and recreates its entry
   cloudify tunnel list
   ```

To run a tunnel on another machine **in addition** to this one, use replicas instead of copying the folder
([Getting started → Redundancy](getting-started.md#redundancy-one-tunnel-several-machines)).

## Resetting things

| Goal | How |
| --- | --- |
| Forgot the dashboard password | Stop the dashboard, delete or edit `~/.cloudflared/cloudify/users.json`, start it again and create a new first account with the setup code it prints. Other accounts in the file are lost if you delete it. |
| Start Cloudify's settings from scratch, keep tunnels | Delete `~/.cloudflared/cloudify/` (users, connections, tokens). Reconnect domains afterwards. |
| Remove a tunnel completely | `cloudify tunnel delete <name>` (Cloudflare, config, credentials, auto-start, and DNS with cleanup on). |
