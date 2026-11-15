# CLI reference

Every command prints its own help with `--help`, e.g. `cloudify route add --help`. Commands that take a
tunnel name use the *current* tunnel when it is left out; set it with `cloudify tunnel use <name>`.

```text
cloudify [--version] [-p PORT] [-ssh host|remote]
         setup | quickstart | serve | tunnel | route | account | replica | temp | cloudflared | ssh
```

## Top-level options

| Command | What it does |
| --- | --- |
| `cloudify -p 3000` | Temporary public URL (`https://<random>.trycloudflare.com`) for `localhost:3000` until Ctrl+C. No account needed. |
| `cloudify -ssh host` | Share this machine's shell temporarily ([SSH sharing](ssh.md)). |
| `cloudify -ssh remote` | Connect to a machine that is sharing its shell. |
| `cloudify --version` | Show the version. |

## Setup

| Command | What it does |
| --- | --- |
| `cloudify setup --domain example.com` | Connect a Cloudflare domain: prints an authorization link, waits until you approve it. |
| `cloudify quickstart [NAME]` | Interactive wizard: tunnel name, auto-start, first route. |
| `cloudify serve [--serve-port 8765] [--host ADDR]` | Start the dashboard and API on `127.0.0.1` (this machine only). `--host 0.0.0.0` listens on every network interface and then needs TLS (`--ssl-certfile`/`--ssl-keyfile`) or an explicit `--insecure-http`. `--allowed-host NAME` adds a host name it may be opened under (e.g. a tunnel hostname); `--api-docs` enables the interactive API docs at `/api/docs`. While no dashboard account exists it prints the one-time setup code needed to create the first one. See [Security](security.md). |

## `cloudify tunnel`

| Command | What it does |
| --- | --- |
| `tunnel create [NAME] [--no-autostart] [-a ACCOUNT]` | Create a tunnel in Cloudflare and set up auto-start. `-a` picks the Cloudflare account (ID or one of its domains) when several are connected. Names: letters, digits, `-`, `_`, `.`, up to 63 characters. |
| `tunnel list` | All tunnels Cloudify manages (status, routes, auto-start, whether they still exist in Cloudflare) and other tunnels of the connected accounts. |
| `tunnel status [NAME]` | Details of one tunnel: process, connections, routes. |
| `tunnel start [NAME]` / `tunnel stop [NAME]` | Start or stop it (through the auto-start service when one is installed). |
| `tunnel use NAME` | Make NAME the current tunnel for later commands. |
| `tunnel autostart [NAME] --enable / --disable / --status` | Manage starting at boot (systemd, cron, launchd or Task Scheduler). |
| `tunnel adopt NAME` | Manage a tunnel that was created outside Cloudify (e.g. with `cloudflared tunnel create`). |
| `tunnel delete [NAME] [--force]` | Stop it, remove auto-start, delete it in Cloudflare and (with DNS cleanup on) its DNS records. Refuses while replicas are attached. |
| `tunnel resume` | Start every tunnel with auto-start enabled (used by the Docker entrypoint). |

## `cloudify route`

| Command | What it does |
| --- | --- |
| `route add DOMAIN PORT [-t TUNNEL] [--service HOST]` | Publish `https://DOMAIN` → `http://HOST:PORT` (HOST defaults to `localhost`; use a container name or IP otherwise). Creates the DNS record and starts or restarts the tunnel. `DOMAIN` may be a wildcard (`*.example.com`). |
| `route edit DOMAIN [-t TUNNEL] [--hostname NEW] [--service HOST] [--port N]` | Change hostname, service host or port. A new hostname gets its DNS record; the old one is deleted when DNS cleanup is on. A running tunnel is restarted; a stopped one stays stopped and uses the change when it starts. |
| `route remove DOMAIN [-t TUNNEL]` | Remove the route (and its DNS record with DNS cleanup on). A running tunnel is restarted; a stopped one stays stopped. |
| `route list [-t TUNNEL]` | Show the routes. |
| `route check [-t TUNNEL]` | Check that every route's DNS record exists and points at the tunnel. |
| `route repair DOMAIN [-t TUNNEL] [--overwrite]` | Recreate a missing DNS record; `--overwrite` re-points a record that points elsewhere. |

Examples:

```bash
cloudify route add app.example.com 3000 -t web
cloudify route add api.example.com 8080 -t web --service my-api        # a container on the same network
cloudify route add '*.dev.example.com' 8000 -t web                    # wildcard (quote it for the shell)
cloudify route edit app.example.com --port 3001
cloudify route edit app.example.com --hostname www.example.com
```

## `cloudify account`

| Command | What it does |
| --- | --- |
| `account connect DOMAIN` | Authorize another domain (same or another Cloudflare account). |
| `account list` | Connected domains with their connection ID and account ID. |
| `account token CONNECTION_ID [--remove]` | Store (or forget) a Cloudflare API token with `Zone · DNS · Edit` for automatic DNS cleanup. The token is checked with Cloudflare before it is saved; it is read from a hidden prompt. |
| `account domain CONNECTION_ID DOMAIN` | Name the domain of a connection made by an older version (the `default` connection). |
| `account remove CONNECTION_ID` | Disconnect a domain. Running tunnels keep running. |

## `cloudify replica`

| Command | What it does |
| --- | --- |
| `replica export OUTPUT -t TUNNEL [-t ...] [--passphrase P]` | Write an encrypted file (scrypt + AES-256-GCM) that lets another machine run these tunnels. Asks for the passphrase if not given. |
| `replica import FILE [--passphrase P] [--no-autostart]` | Run the tunnels from that file on this machine. No Cloudflare login needed. |
| `replica plan [-t TUNNEL] [-H HOSTNAME]` | Show what replicating whole tunnels or single hostnames involves. |
| `replica split TUNNEL HOSTNAMES... --name NEW` | Move some hostnames into a new tunnel (their DNS records are re-pointed, no downtime) so they can be replicated alone. |

Typing the passphrase at the prompt is safer than `--passphrase` (which ends up in your shell history).

## `cloudify temp`

| Command | What it does |
| --- | --- |
| `temp create PORT` | Same as `cloudify -p PORT`. |
| `temp list` | Temporary tunnels running on this machine. |
| `temp stop [URL]` | Stop one temporary tunnel, or all of them. |

## `cloudify cloudflared`

| Command | What it does |
| --- | --- |
| `cloudflared version` | Installed and latest cloudflared version. |
| `cloudflared update` | Update cloudflared (or print the Homebrew/apt/dnf command when a package manager owns it). |

## `cloudify ssh` (also `cloudify -ssh`)

| Command | What it does |
| --- | --- |
| `ssh host [--ttl MIN] [--once] [--save-code FILE]` | Share this machine's shell. Prints an access code, a password and a one-line remote command. Ctrl+C stops sharing at once. `--ttl` (default 60, max 1440 minutes), `--once` (stop after the first session). |
| `ssh remote [--code CODE \| --code-file FILE] [--password PW] [--sftp] [-- COMMAND]` | Connect: asks for whatever is not given. `--sftp` opens a file-transfer session; `-- uptime` runs one command. |

Details and the security model: [SSH sharing](ssh.md).

## Environment variables

| Variable | Used by | Purpose |
| --- | --- | --- |
| `CLOUDIFY_SSH_PASSWORD` | `ssh remote` | Password for scripts (people should type it). |
| `CLOUDIFY_SETUP_CODE` | `serve` | Use this first-run setup code instead of a random one (scripted installs; use a long random value). |
| `CLOUDIFY_ALLOWED_HOSTS` | `serve` | Extra host names the dashboard may be opened under, comma-separated (same as `--allowed-host`). |
| `CLOUDIFY_INSECURE_HTTP=1` | `serve` | Same as `--insecure-http`. |
| `CLOUDIFY_API_DOCS=1` | `serve` | Same as `--api-docs`. |
| `CLOUDIFY_SERVE_ARGS` | Docker image | Options for `cloudify serve` in the container (default `--insecure-http`; see [Docker](docker.md#securing-the-dashboard)). |
| `HOME` | everything | Data lives in `$HOME/.cloudflared` ([Files and data](files-and-data.md)). |

## Exit codes

`0` on success, `1` when the operation failed (the message says why), `2` for invalid command-line usage.
