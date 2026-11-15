# Updating and uninstalling

## Update (Linux and macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh | bash -s -- --update
```

or, with a copy of the installer, `bash install.sh --update`. This works from **any earlier version**, including
Cloudify 1.x installed with the old `install_scripts/final.sh` or `mac.sh`. Re-running the installer without
`--update` does the same work; `--update` only makes the intent explicit and reports what changed.

What happens, step by step:

1. **Finds the current installation**: pipx (`cloudify-cli` environment), the virtualenv fallback
   (`~/.local/share/cloudify/venv`) or a 1.x copy installed with `pip install --user`, and its version.
2. **Backs up the settings** to `~/.cloudflared/cloudify/backups/before-update-<date>.tar.gz` (readable only by
   you; the five newest are kept): the tunnel list (`tunnel_manager_state.json`), tunnel configs
   (`config-*.yml`), dashboard users and Cloudflare connections. Tunnel credentials and certificates are not
   changed by an update and are not copied.
3. **Updates the prerequisites** that are missing or too old, and **cloudflared** when a newer release exists.
4. **Replaces the program.** A 1.x copy installed with `pip install --user` is removed first (the files it
   recorded, plus the Node.js packages the 1.x dashboard downloaded into its folder); folders that contain files
   from other software are left in place and listed.
5. **Moves 1.x dashboard accounts.** 1.x kept `users.json` in whatever folder `cloudify serve` was started from.
   The installer looks in your home folder, the current folder and the working folder of a running 1.x
   dashboard, and copies the first valid file (owned by you, not writable by others) to
   `~/.cloudflared/cloudify/users.json`. Existing 2.x accounts are never overwritten.
6. **Reports** the old and new version, how many tunnels were kept, and whether a dashboard is still running
   the old version.

What is **not** interrupted or changed:

* **Running tunnels keep running** during the update. A tunnel picks up a new cloudflared version the next
  time it restarts.
* Tunnels, routes, DNS records, auto-start entries, Cloudflare logins and dashboard users are kept. The
  auto-start entries of every version have the same names, so the new version recognizes them.

After updating:

* If the installer says *the dashboard is still running the previous version*, restart it: stop it (Ctrl+C in its
  terminal, `kill <pid>`, or `systemctl --user restart cloudify-dashboard` if you made a service) and start
  `cloudify serve` again. Everyone signed in has to sign in again.
* Open a new terminal if `cloudify --version` still shows the old version (your shell cached the old path).

### From Cloudify 1.x: what is different

| 1.x | Now |
| --- | --- |
| Node.js/Next.js frontend on port 8766 plus an API on 8765 | One built-in dashboard on port 8765, no Node.js |
| `users.json` in the folder you started `cloudify serve` from | `~/.cloudflared/cloudify/users.json` (moved by the installer, or on first start) |
| One Cloudflare login (`~/.cloudflared/cert.pem`) | Several domains and accounts; the old certificate becomes the first connection (name it with `cloudify account domain default example.com`) |
| `cloudify serve --next-frontend`, `--frontend-port`, `--api-host` | `cloudify serve [--serve-port 8765] [--host ADDR]` |
| Dashboard on all interfaces (`0.0.0.0`) over plain HTTP by default | `127.0.0.1` by default; the network needs HTTPS or `--insecure-http` ([Security](security.md)) |
| Windows-specific `serve --windows` | Same commands on every platform |

Node.js is no longer needed; you can remove it if nothing else uses it.

## Update in Docker

```bash
cd Cloudify_v2 && git pull
docker compose -f docker/docker-compose.yml up -d --build
```

Data stays in the volume. Coming from the 1.x container (no volume), follow
[Moving from the Cloudify 1.x container](docker.md#moving-from-the-cloudify-1x-container).

## Update on Windows (native)

```powershell
pipx install --force https://github.com/yash7158/Cloudify_v2/archive/refs/heads/main.zip
```

In WSL, use the Linux command above.

## Update only cloudflared

```bash
cloudify cloudflared version       # installed vs latest
cloudify cloudflared update        # updates the binary, or prints the brew/apt/dnf command to use
```

Restart tunnels afterwards (`cloudify tunnel stop web && cloudify tunnel start web`, or reboot) so they use it.

## Going back to the previous settings

Every update leaves a backup. To restore one (stop the dashboard first):

```bash
ls ~/.cloudflared/cloudify/backups/
tar -xzf ~/.cloudflared/cloudify/backups/before-update-20261115-120000.tar.gz -C ~/.cloudflared
```

To install an older program version: `bash install.sh --branch <tag-or-branch>`.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/install.sh | bash -s -- --uninstall
```

This removes the program (pipx environment, virtualenv, launcher, and a 1.x `pip --user` copy) and keeps:

* your tunnels, which **keep running** (and keep auto-starting) because they are cloudflared processes;
* `~/.cloudflared` with configs, credentials, logins and Cloudify's settings;
* cloudflared itself.

To remove everything at once, use the [safety check](security.md#safety-check). It stops all tunnels,
removes the auto-start entries, and uninstalls Cloudify and cloudflared. Add `--purge` to also delete
`~/.cloudflared`:

```bash
curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/safety-check.sh | bash -s -- --remove
```

Or step by step:

1. Delete tunnels you no longer need **before** uninstalling: dashboard → *Delete*, or
   `cloudify tunnel delete <name>` (also removes auto-start, and DNS records when DNS cleanup is on).
2. Uninstall with `--uninstall --purge`, which also deletes `~/.cloudflared/cloudify` (dashboard users,
   connections, backups).
3. Optionally remove the rest by hand: `rm -rf ~/.cloudflared` (all cloudflared logins and tunnel
   credentials, also those not created by Cloudify!) and cloudflared (`sudo rm /usr/local/bin/cloudflared`,
   `brew uninstall cloudflared`, or your package manager).
