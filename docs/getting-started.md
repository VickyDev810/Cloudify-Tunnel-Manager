# Getting started

This guide explains the ideas behind Cloudify and walks you from a fresh installation to a website on
your own domain. Install Cloudify first: [Linux](install-linux.md), [macOS](install-macos.md),
[Docker](docker.md) or [Windows](install-windows.md).

## The ideas in two minutes

| Term | Meaning |
| --- | --- |
| **cloudflared** | Cloudflare's connector program. It runs on your machine and keeps outbound connections to Cloudflare open; visitors reach your service through those connections, so no port has to be opened. Cloudify starts and supervises it for you. |
| **Temporary tunnel** (quick tunnel) | A random `https://<words>.trycloudflare.com` address for a local port. No account, no domain, gone when stopped. For demos and testing. |
| **Tunnel** (named tunnel) | A permanent connector with an ID, created in your Cloudflare account. One tunnel can publish many hostnames. |
| **Route** | One public hostname (`app.example.com`, or a wildcard `*.example.com`) mapped to a local service (`http://localhost:3000`, `http://my-container:80`). Adding a route also creates the DNS record. |
| **Connection** (connected domain) | The authorization Cloudify gets when you log in to Cloudflare and pick a domain. You can connect many domains, also from different Cloudflare accounts. |
| **Replica** | The same tunnel running on a second (third, ...) machine. Cloudflare spreads traffic across them and skips a machine that goes down. |
| **Auto-start** | The tunnel starts again by itself after a reboot (systemd, cron, launchd or the Windows task scheduler, chosen automatically). |
| **Dashboard** | The web interface started with `cloudify serve` (default `http://localhost:8765`). Everything in this guide can be done there or on the command line. |

## Option 1: a temporary public URL (no account needed)

Start something on a local port, then:

```bash
cloudify -p 3000
```

After a few seconds Cloudify prints an address such as `https://tiger-river-cloud-example.trycloudflare.com`.
Anyone with the link can reach `localhost:3000` until you press Ctrl+C. More: `cloudify temp list`,
`cloudify temp stop [URL]`. The address is random; for a stable name use your own domain (option 2).

## Option 2: your own domain on the command line

You need a domain whose DNS is managed by Cloudflare
([add a site to Cloudflare](https://developers.cloudflare.com/fundamentals/manage-domains/add-site/),
the free plan is fine).

**1. Connect the domain** (once per domain):

```bash
cloudify setup --domain example.com
```

Cloudify runs `cloudflared tunnel login` and prints a `https://dash.cloudflare.com/argotunnel?...` link.
Open it in any browser (it doesn't have to be on the same machine), sign in, and click your domain.
The command finishes by itself when Cloudflare confirms. For more domains use
`cloudify account connect other-domain.com`; see them with `cloudify account list`.

**2. Create a tunnel:**

```bash
cloudify tunnel create web
```

`web` is any name (letters, digits, `-`, `_`, `.`). By default auto-start is set up as well; add
`--no-autostart` to skip it. With several Cloudflare accounts connected, choose one with
`--account example.com`.

**3. Add a route** (public hostname → local port):

```bash
cloudify route add app.example.com 3000 -t web
# a container or another machine on the network instead of localhost:
cloudify route add api.example.com 8080 -t web --service my-api-container
```

This writes the tunnel's configuration, creates the DNS record (`CNAME app → <tunnel-id>.cfargotunnel.com`)
and (re)starts the tunnel. Open `https://app.example.com`: Cloudflare provides the HTTPS certificate.

**4. Check it:**

```bash
cloudify tunnel list            # all tunnels with status, routes and auto-start
cloudify tunnel status web
cloudify route list -t web
cloudify route check -t web     # are the DNS records there and pointing at this tunnel?
```

**Day-to-day commands:** `cloudify tunnel stop web`, `cloudify tunnel start web`,
`cloudify route edit app.example.com --port 3001`, `cloudify route remove app.example.com`,
`cloudify tunnel delete web`. The `-t web` option can be left out after `cloudify tunnel use web`.

There is also an interactive wizard that asks for everything: `cloudify quickstart`.

## Option 3: the same in the dashboard

```bash
cloudify serve                  # then open http://localhost:8765
```

1. **Create the first account** with the setup code that `cloudify serve` printed in the terminal
   (`🔑 Setup code: ...`). This account is the administrator; see [Security](security.md) before exposing the
   dashboard.
2. **Connect Cloudflare**: click *Connect Cloudflare*, open the link, pick your domain.
3. **Tunnels → New tunnel**, then **Add route** on the tunnel card.

The [dashboard guide](dashboard.md) describes every page.

## DNS records and the API token

Adding a route creates a DNS record through cloudflared. cloudflared cannot delete records, so by default
removing a route, renaming a hostname or deleting a tunnel leaves the old record in Cloudflare (it then
shows Cloudflare's error page). Give Cloudify an API token to clean up automatically:

1. Open [dash.cloudflare.com → My Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens) →
   **Create Token** → **Edit zone DNS** → **Use template**.
2. Keep the permission `Zone · DNS · Edit`. Under *Zone Resources* choose `Include · Specific zone · example.com`.
3. Optionally limit *Client IP Address Filtering* to your server's IP and set an expiry date.
4. **Continue to summary → Create Token**, copy the token (it is shown only once).
5. Add it: dashboard **Domains → DNS cleanup**, or `cloudify account token <connection-id>`
   (the ID is shown by `cloudify account list`).

Cloudify only deletes a `CNAME` that points at that exact tunnel; records you changed to point elsewhere
are left alone. Without a token (or after *Turn off and forget the token*) it never deletes DNS records. If a record was deleted by mistake: `cloudify route repair app.example.com -t web`
(or **Tunnels → Check DNS → Recreate**).

## Several domains and Cloudflare accounts

Each login authorizes one domain. Connect as many as you need, from the same or different Cloudflare
accounts. A tunnel belongs to one account and can serve hostnames on every domain of that account.

```bash
cloudify account connect example.com
cloudify account connect shop.net          # may be another Cloudflare account
cloudify tunnel create store --account shop.net
cloudify route add www.shop.net 8080 -t store
```

## Starting at boot

`cloudify tunnel create` sets up auto-start unless you pass `--no-autostart`. Change it any time:

```bash
cloudify tunnel autostart web --enable      # or --disable, --status
```

| System | Mechanism | Starts |
| --- | --- | --- |
| Linux with systemd | user service `cloudflared-tunnel-<name>` + `loginctl enable-linger` | at boot, without logging in |
| Linux without systemd | `@reboot` line in your crontab + `~/.local/bin/start-tunnel-<name>.sh` | at boot |
| macOS | LaunchAgent `com.user.cloudflare.tunnel.<name>` | when you log in |
| Windows | scheduled task `CloudflaredTunnel-<name>` | when you log in |
| Docker | container restart policy + `cloudify tunnel resume` at container start | when Docker starts |

Auto-start covers tunnels. To keep the **dashboard** running as well, see the service examples in
[Install on Linux](install-linux.md#run-the-dashboard-as-a-service) and
[Install on macOS](install-macos.md#run-the-dashboard-as-a-service), or use [Docker](docker.md).

## Redundancy: one tunnel, several machines

Run the same tunnel on a second machine and Cloudflare balances between them and fails over automatically:

```bash
# machine A (has the tunnel)
cloudify replica export web-replica.json -t web     # asks for a passphrase
# copy the file to machine B, then on machine B (no Cloudflare login needed)
cloudify replica import web-replica.json
```

Both machines need the same local services (for example something listening on port 3000). Routes are
managed on machine A; after changing them, export and import again. The dashboard's **Redundancy** page
shows every machine connected for each tunnel. To make only some hostnames redundant, move them into
their own tunnel first: `cloudify replica split web app.example.com --name web-ha`.

## Next steps

* [CLI reference](cli-reference.md): every command and option.
* [Dashboard](dashboard.md): users, domains, redundancy status.
* [SSH sharing](ssh.md): give someone temporary shell access with one command.
* [Files, data and backups](files-and-data.md) and [Security](security.md).
