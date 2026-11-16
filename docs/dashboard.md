# Dashboard

The dashboard is a web interface built into Cloudify: no Node.js, no separate frontend server, the same on
Linux, macOS, Windows and Docker. Everything it does is also available on the [command line](cli-reference.md).

```bash
cloudify serve                                   # http://localhost:8765 and this machine's IP addresses, any name
cloudify serve --serve-port 9000                 # another port
cloudify serve --ssl-certfile cert.pem --ssl-keyfile key.pem   # HTTPS
cloudify serve --secure                          # this machine only, known names only (see Security)
```

To keep it running permanently, see *Run the dashboard as a service* for
[Linux](install-linux.md#run-the-dashboard-as-a-service) and [macOS](install-macos.md#run-the-dashboard-as-a-service),
or use [Docker](docker.md), where it is the container's main process.

## First run

1. **Create the first account.** The setup page asks for the **setup code** that `cloudify serve` prints when it
   starts while no account exists (`🔑 Setup code: ABCDE-FGH23`; with Docker: `docker logs cloudify`). Only
   someone who can see the server's console can create the first account, which becomes the administrator.
   Upper/lower case and the dash don't matter. Passwords need at least 6 characters.
2. **Connect Cloudflare.** Click *Connect Cloudflare*. Cloudify runs `cloudflared tunnel login` and shows a link;
   open it (on any device), sign in to Cloudflare and click the domain to authorize. The dashboard notices when
   you are done. You can skip this and do it later on the **Domains** page.
3. **Create a tunnel and add routes** on the **Tunnels** page.

Signing in gives your browser a session that lasts up to 7 days. Restarting the dashboard signs everyone out;
changing a password signs out every other session of that account at once, including sign-ins that were still in
progress.

## Pages

### Overview

A live map of how traffic flows: Cloudflare → your hostnames → tunnels → local services, with counts of tunnels,
routes and domains, and a notice when a newer cloudflared is available.

### Tunnels

* **New tunnel**: name, Cloudflare account (when several are connected), and whether it starts at boot.
* Per tunnel: **Start/Stop**, **Start on boot** switch, **Add route**, edit a route (pencil: hostname, service
  host, port), remove a route, **Check DNS** (with **Recreate** / **Re-point** for broken records), **Delete**.
* **Adopt** a tunnel that was created outside Cloudify (for example with `cloudflared tunnel create`).

A route's *service host* is `localhost` for services on the same machine, an IP address for another machine,
or a container name when Cloudify runs in Docker (see [Docker](docker.md#routing-to-your-services)). The port
is the service's local port; `http://` is added automatically. Wildcard hostnames (`*.example.com`) are allowed.

### Routes

All hostnames of all tunnels, grouped by domain, with search and a domain filter.

### Domains

* **Connect domain**: authorize another domain, from the same or another Cloudflare account.
* **DNS cleanup**: add a Cloudflare API token (`Zone · DNS · Edit`) per domain so DNS records are deleted
  automatically when you remove routes or tunnels. The page shows how to create the token; it is checked with
  Cloudflare before it is saved and never shown again.
* **Disconnect** a domain (running tunnels keep running).

### Redundancy

* **Status**: every machine connected to Cloudflare for each shared tunnel, with its IP, healthy edge connections
  (4/4 is normal), Cloudflare data centers, cloudflared version and uptime; *this machine* is marked. Refresh with
  the refresh button. A machine sees the other machines only if the tunnel's domain is connected on it; otherwise it
  shows its own connection health.
* **Share from this machine**: choose whole tunnels, whole domains or single hostnames, set a passphrase and
  download the encrypted replica file. Hostnames that share a tunnel with others are first moved to their own
  tunnel (the dialog explains this before doing it).
* **Import**: upload a replica file and its passphrase to run those tunnels on this machine.
* Warnings when replicas are outdated (routes changed after the export).

### Settings

* **Change password** (signs out your other sessions).
* **Users**: add people and remove them. Every user can manage everything; there are no read-only roles.
* **Cloudflare**: connect another domain.
* **cloudflared**: installed vs latest version, update button.
* **Stop server**.

## Themes and devices

Dark theme by default and a light theme (the sun/moon button switches and remembers your choice), and a layout
that works on phones and tablets.

## API

Everything the dashboard does goes through a JSON API on the same port. Interactive documentation: start
with `cloudify serve --api-docs` and open `http://localhost:8765/api/docs` (off by default: the page loads
Swagger UI from a CDN). Authenticate with `POST /auth/login` (form fields `username`, `password`),
then send `Authorization: Bearer <access_token>`:

```bash
TOKEN=$(curl -s -X POST http://localhost:8765/auth/login -d 'username=admin' -d 'password=...' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8765/tunnels
```

## When something doesn't work

* *Page doesn't load from another device*: the dashboard listens on every interface unless it runs with
  `--secure`. Check the firewall (`sudo ufw allow 8765/tcp`). On AWS or Azure, allow TCP port 8765 in the security
  group or network security group. With `--secure`, use an SSH tunnel ([Security](security.md)).
* *"This dashboard runs in secure mode and can't be opened as ..."*: in secure mode it only answers to known names
  (protection against DNS rebinding). Use the IP address or `localhost`, or add the name with
  `--allowed-host NAME`. For quick tunnels, whose name changes every time, use
  `--allowed-host '*.trycloudflare.com'` or the `CLOUDIFY_ALLOWED_HOSTS` environment variable. Or start it
  without `--secure`.
* *"Address already in use"*: another program (or another dashboard) uses the port; pick another with
  `--serve-port`.
* More in [Troubleshooting](troubleshooting.md).
