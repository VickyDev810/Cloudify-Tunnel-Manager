# Security

Cloudify controls your Cloudflare tunnels, DNS records and the services you publish, so treat it like an
administration tool: keep the dashboard private, keep `~/.cloudflared` private, and publish only what you mean
to publish.

## The dashboard

**Default: open to the network.** `cloudify serve` and the Docker image listen on port 8765 of every interface
(`0.0.0.0`) and answer under any address: an IP address (a cloud server's public IP, for example), a domain or
a quick tunnel. Unless you give it a certificate, that's plain HTTP. Anyone who can reach the port can open the
login page, and passwords and login tokens cross the network unencrypted. `cloudify serve` warns about this when it
starts. On AWS or Azure, the security group or network security group decides who can reach port 8765: allow only
your own IP address if you can.

**Secure mode** (`cloudify serve --secure`; Docker: `CLOUDIFY_SECURE=1`, see
[Docker → Securing the dashboard](docker.md#securing-the-dashboard)):

* it listens on `127.0.0.1` only, unless you give `--host`;
* it answers only to known names (see *DNS rebinding* below);
* it refuses plain HTTP on a network address unless you add `--insecure-http`.

To use the dashboard from another device, choose one of these, from most to least secure:

| Option | How |
| --- | --- |
| **SSH tunnel** | Run the dashboard with `--secure`. Then `ssh -L 8765:localhost:8765 user@server`, and open `http://localhost:8765` on your computer |
| **Cloudflare Access** | Add a route `dash.example.com → localhost:8765` and protect `dash.example.com` with an Access application (Zero Trust → Access → Applications, allow only your e-mail). Run the dashboard with `--secure`: the names of your routes are known names. Traffic is HTTPS end to end through Cloudflare, and nobody reaches the login page without passing Access. |
| **HTTPS on your network** | `cloudify serve --ssl-certfile cert.pem --ssl-keyfile key.pem`, with a certificate from your own CA or e.g. `mkcert`. Add `--secure --host 0.0.0.0` to accept only known names. |
| **Plain HTTP** (the default) | `cloudify serve`. Passwords and login tokens cross the network unencrypted; anyone who can watch that network can take over the dashboard. Use it only behind a TLS proxy or on a network you fully trust. |

**First run.** The first account (the administrator) can only be created with the one-time **setup code** that
`cloudify serve` prints on its console while no account exists (`docker logs cloudify` in Docker). Someone who
reaches the dashboard over the network, through a proxy or tunnel, or as another user of the same machine can't
claim it. For scripted installs the code can be set with `CLOUDIFY_SETUP_CODE`; use a long random value.

**Users.** Every account can do everything (add routes, delete tunnels, add and remove users, stop the server).
Only give accounts to people you would give your Cloudflare account to. Passwords need at least 6 characters;
use long ones. There is no password reset; see [Files and data](files-and-data.md#resetting-things).

**Sessions** last up to 7 days and are kept in the browser's local storage. Signing out, changing the password
(signs out every other session, including sign-ins still in progress), deleting a user (also if the name is
created again later) and restarting the dashboard end them.

**Built-in protections:**

* *DNS rebinding (secure mode):* the dashboard only answers requests addressed to an IP address, `localhost`, this
  machine's name, the hostnames of your tunnels' routes, and names given with `--allowed-host`. A web page you visit can't
  point its own domain at your dashboard and drive it through your browser. `--allowed-host` also takes patterns,
  such as `*.trycloudflare.com` for every quick tunnel. Use a pattern only for a domain whose DNS you, or a
  provider you trust, control. Patterns broader than `*.example.com`, like `*.com`, are refused. A pattern only
  lets the dashboard be opened under those names: another site under them still can't send it changes. By
  default (open mode) every name is accepted.
* *Cross-site requests:* a request that changes something (sign-in, account creation, every action) is refused
  when the browser says it comes from another site. Together with login tokens that are never sent automatically
  (no cookies), a web page you visit can't act on the dashboard.
* *Framing and content injection:* every response carries `Content-Security-Policy` (scripts from the dashboard
  only, no framing), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
  All values are escaped before they are displayed.
* *Fail closed:* if the user list (`users.json`) can't be read, the dashboard refuses logins and registration
  instead of treating it as "no users yet".
* The interactive API documentation (`/api/docs`) is off unless you start with `--api-docs`. It then loads
  Swagger UI from a CDN, pinned to an exact version and checked by the browser against SHA-384 hashes (Subresource
  Integrity), so a changed file is refused.
* A sign-in for an unknown user name takes as long as a wrong password, so user names can't be probed.
* Request bodies larger than 256 KiB are refused before they are read, and a request that changes something
  without a valid login token is refused before its body is even parsed (only signing in and creating the first
  account are public). At most 64 requests are handled at once. Nobody can exhaust the server's memory without
  signing in.

## Secrets on disk

Everything is in `~/.cloudflared` ([Files and data](files-and-data.md)). Cloudify writes its own secret files
readable by you only (`600`): dashboard users, connected accounts with DNS API tokens, certificates of extra
domains, imported tunnel credentials, the settings backups of the installer, and access codes saved with
`--save-code`. Tunnel credentials made by cloudflared itself are `400`. Recommended:

```bash
chmod 700 ~/.cloudflared
```

Anyone who can read `<tunnel-id>.json` can run your tunnel and receive part of its traffic; anyone with
`cert.pem` can manage tunnels and DNS for that domain's account. Never commit `~/.cloudflared`, put it in an
image, or paste its files into issues. Backups contain the same secrets.

## Replica files

An exported replica file contains the tunnel's credentials, encrypted with your passphrase (scrypt +
AES-256-GCM). Use a long passphrase (at least 8 characters are required; prefer a phrase of several words), send
the file and the passphrase separately, and delete the file after importing it. Import only files you exported
yourself: a replica file decides which local services the tunnel forwards to on the importing machine (only
`http://` and `https://` services are accepted).

## Cloudflare API token (DNS cleanup)

Create the token with the **Edit zone DNS** template, limited to the one zone it is for. Optionally restrict it to
your server's IP address (*Client IP Address Filtering*) and give it an expiry date. Cloudify checks it against the
zone before saving it, never shows it again, and only uses it to read and delete DNS records of that zone; it only
ever deletes a `CNAME` that points at the exact tunnel being changed.

## What you publish

Every route makes a local service reachable from the internet, without a login unless the service has one. For
admin panels, databases, home-automation and other private services, add a **Cloudflare Access** policy to the
hostname in the Cloudflare dashboard (Zero Trust → Access → Applications). Temporary tunnels (`cloudify -p`) are
public too, with a hard-to-guess but unauthenticated address: stop them when you are done. The
[safety check](#safety-check) lists every one that is still running. They end with the
command (Ctrl+C, `kill`, or closing the terminal); if the command was killed hard, `cloudify temp stop` stops what
is left. They use a configuration of their own, never `~/.cloudflared/config.yml`, so they publish exactly the
port you gave.

## Safety check

`safety-check.sh` (in the repository root) answers "what on this machine is reachable through Cloudflare
right now?" and can stop it. It needs only bash and standard tools, not Cloudify, so it also works after
Cloudify was removed or on a machine where someone else started tunnels. It runs on Linux and macOS, and
on Windows inside WSL.

```bash
curl -fsSL https://raw.githubusercontent.com/yash7158/Cloudify_v2/main/safety-check.sh | bash
```

Without options it **only lists**:

| Section | What it shows |
| --- | --- |
| Temporary tunnels | Every `cloudflared` quick tunnel: its public `trycloudflare.com` address (asked from cloudflared itself), the local service it forwards to, and what started it (`cloudify -p`, the dashboard) |
| SSH sharing | Every `cloudify -ssh host`: its public address, and the programs that connected remotes are running |
| Outgoing connections | `cloudify -ssh remote` sessions and other `cloudflared access` connections from this machine |
| Persistent tunnels | Named tunnels, their Cloudflare connections, and what keeps them running (systemd service, launchd, cron, by hand, a Docker container) |
| Cloudify dashboard | Running dashboards and their listening addresses. A dashboard reachable from the network, or one using plain HTTP, is flagged |
| Starts automatically | Cloudify's auto-start entries (systemd user units, cron, launchd) and a system-wide cloudflared service |
| Installed | The `cloudify` and `cloudflared` programs, what `~/.cloudflared` contains, and Docker containers |

Tunnel tokens and credentials are never printed.

| Option | What it does | Asks first |
| --- | --- | --- |
| `--kill` | Stops temporary tunnels, SSH sharing (all remotes are disconnected) and outgoing sessions | no |
| `--kill-all` | Also stops persistent tunnels (through their service manager, so they don't restart until the next boot) and the dashboard | yes |
| `--remove` | Stops everything, removes the auto-start entries, uninstalls Cloudify (pipx, virtualenv, a 1.x `pip --user` copy) and cloudflared (Homebrew, your package manager, or the binary) | yes |
| `--remove --purge` | Also deletes `~/.cloudflared` (cert.pem, tunnel credentials, Cloudify's accounts and users) and `/etc/cloudflared` | you type `delete` |
| `-y`, `--yes` | Skips the questions (for scripts). Without a terminal, `--kill-all` and `--remove` refuse to run unless it's given | |

Good to know:

* **Users:** only your own processes are stopped. Run it with `sudo` to stop other users' processes too.
  Don't use `sudo` for `--remove`: it would remove root's installation. Without sudo, it asks for your
  password only for system-wide parts such as `/usr/local/bin/cloudflared`.
* **Docker:** processes inside Docker containers are listed but not stopped. Use
  `docker compose -f docker/docker-compose.yml down` (and `docker volume rm cloudify-data` to delete its
  data).
* **Cloudflare account:** `--purge` deletes the local keys only. The tunnels and DNS records stay in your
  Cloudflare account. Delete them first if you no longer need them (`cloudify tunnel delete NAME`, or
  Cloudflare dashboard → Zero Trust → Networks → Tunnels).
* **PATH lines:** the lines that `install.sh` added to `~/.profile`, `~/.bashrc` or `~/.zshrc` are left. They
  only add `~/.local/bin` to `PATH`.

## SSH sharing

`cloudify -ssh host` gives a shell **as your user** to whoever has both the access code and the password. Details
are in [SSH sharing](ssh.md#how-safe-is-it): one-time keys, key-only authentication (no passwords, no Kerberos),
host key pinning, loopback-only server, everything ends with Ctrl+C. The remote validates every field of the
access code before using it, so a malicious code can't make `ssh` run commands on the connecting machine.

## Installer and updates

* The installer is a script you pipe into `bash`: read it first if you like (`curl -fsSLO .../install.sh`), and
  pin a released version with `--branch v2.1.0` instead of `main`.
* It uses `sudo` only to install system packages and to put cloudflared into `/usr/local/bin`.
* cloudflared is downloaded over HTTPS from Cloudflare's GitHub releases and checked against the SHA-256
  checksum published in the release notes when one is available.
* Updates keep a backup of your settings and never touch running tunnels.

## Docker

The container runs as root inside the container and holds all secrets in the `/root/.cloudflared` volume: protect
the volume and its backups like `~/.cloudflared`. The compose file builds the image from your checkout (it never
pulls a third-party image apart from the official `python` base image). It runs with host networking, so by
default the dashboard listens on port 8765 of every interface of the server, over plain HTTP, under any address:

* `CLOUDIFY_SECURE=1` keeps it on this machine only, and to known names;
* `CLOUDIFY_SERVE_ARGS: "--ssl-certfile /certs/cert.pem --ssl-keyfile /certs/key.pem"` gives you HTTPS.

Details are in [Docker](docker.md#securing-the-dashboard). The first account still needs the setup code from
`docker logs cloudify`. The build checks the downloaded cloudflared against the SHA-256 published in its release
notes.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md): report privately through GitHub's *Report a vulnerability*, not in a public
issue.

## Security review (November 2026)

The code was audited with Cloudflare's open-source
[security-audit skill](https://github.com/cloudflare/security-audit-skill) (full audit, standard profile:
reconnaissance, coverage-led hunting in an isolated sandbox, independent verification of every finding) and
reviewed for bugs. The fixes are listed in the release notes; the main ones are summarized here.

**Result.** The review produced 31 possible problems, and each was checked again by an independent reviewer.
15 were confirmed (1 high, 8 medium, 6 low). 13 could neither be proven nor ruled out without a real
deployment, such as a specific browser, a second user account on the machine, or Cloudflare's network. 3 were
real defects without a security impact. **All 31 are fixed in this release**, 29 of them with a regression test
(the other two are a compose setting and the installer's build folder). Since 16 November 2026, two of the fixes
apply only in secure mode, at the owner's request: the dashboard is open to the network by default again (see
the table). The audit looked at a fixed copy of the code before these fixes; the fixes were tested, not audited again.

| Severity | What could happen | What changed |
| --- | --- | --- |
| High | A malicious SSH access code could make `cloudify -ssh remote` run commands on the connecting computer | The remote checks every field of the access code (host name, host key, key format, port, expiry) and passes the host to `ssh` safely |
| Medium | On a new pip/pipx install, anyone on the network could create the first (administrator) account, because the dashboard listened on all interfaces | The first account needs the one-time setup code. The dashboard listened on `127.0.0.1` by default until 16 November 2026. Now it's open to the network by default again, and `--secure` keeps it on this machine |
| Medium | Passwords and login tokens crossed the network as plain HTTP | In secure mode (`--secure`), a network address needs TLS or an explicit `--insecure-http`. By default, plain HTTP on the network is allowed again since 16 November 2026, with a warning when the dashboard starts |
| Medium | Another user of the machine could plant a `users.json` in the folder the dashboard was started from | A 1.x `users.json` is only adopted if it is yours, not writable by others, and holds real password hashes |
| Medium | Someone signing in while you changed the password or deleted the account kept a working session | Changing the password or deleting the user ends every other session, including sign-ins still in progress |
| Medium | `cloudify -ssh remote --password` could run Python files from the current folder | Its restart ignores the current folder |
| Medium | The old install links ran an `install.sh` found next to the current folder | They always download the official installer |
| Medium | The installer's helpers could load Python modules from the current folder | They run isolated, from `/` |
| Medium | Request bodies of any size were read before the login was checked | 256 KiB limit; a change needs a login before its body is read; at most 64 requests at once |
| Low | A domain's DNS API token could appear in the login status that every user sees | The status never holds tokens; old status files are cleaned |
| Low | With the same tunnel name in two accounts, an action could reach the other account's tunnel | Tunnels are matched by their ID and account |
| Low | A process of another user on a tunnel's metrics port was reported as the tunnel's health | Ports held by other users are not trusted |
| Low | A replica file could add options to the auto-start command through its tunnel names | Names in replica files follow the strict rule for new tunnels |
| Low | The API docs loaded an unpinned script from a CDN | Off by default; a pinned version checked with integrity hashes |
| Low | Two temporary tunnels started in the same second shared one record, so `temp stop` missed one | Each tunnel has its own record |

**Not provable offline, fixed anyway:**

* no anti-framing headers;
* DNS rebinding of the dashboard's host name;
* cross-site creation of the first account with older FastAPI versions;
* the public setup check calling Cloudflare;
* an unreadable `users.json` reopening registration;
* Kerberos (GSS-API) logins left enabled on the SSH server;
* secret files briefly readable before `chmod`;
* process checks matching other users' processes;
* replica routes with `bastionMode`;
* temporary tunnels surviving `kill`;
* the compose file pulling a same-named image from Docker Hub;
* the Docker port published on every interface;
* the installer giving pipx a path in `/tmp`.

**Limits.** The audit ran with a fixed budget, so three areas proposed late in the review were not examined. They
were hardened directly instead:

| Area not examined | Hardening |
| --- | --- |
| Connections to the SSH-sharing listener before login | At most 16 connections and a 20-second login limit |
| The Cloudflare login started from the dashboard | A 15-minute limit, with leftovers cleaned up |
| Running out of file handles | Settings files fail closed when they can't be read; at most 64 requests at once |
