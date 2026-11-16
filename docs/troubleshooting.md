# Troubleshooting

Start with these three commands; they answer most questions:

```bash
cloudify --version && cloudflared --version
cloudify tunnel list                 # status, routes, auto-start, and whether each tunnel still exists in Cloudflare
cloudify tunnel status <name>        # process, connections to Cloudflare, routes
```

Logs: `~/.cloudflared/logs/<name>.log` (tunnels started directly), `journalctl --user -u cloudflared-tunnel-<name>`
(systemd), `~/Library/Logs/cloudflared-tunnel-<name>.error.log` (macOS), `docker logs cloudify` (Docker).

## Installation

| Symptom | Cause and fix |
| --- | --- |
| `cloudify: command not found` right after installing | Your shell hasn't picked up the new PATH. Open a new terminal, or run `export PATH="$HOME/.local/bin:$PATH"`. |
| *Python 3.8 or newer is required* | Install a newer Python (`python3.11` or similar) next to the system one; the installer finds it ([Linux notes](install-linux.md#distribution-notes)). |
| Arch: *pacman could not install ...* | Update the system first: `sudo pacman -Syu python python-pipx git`, then run the installer again. |
| *Installing system packages needs root* | Run as a user with `sudo`, or ask an administrator to install the listed packages. |
| *The downloaded cloudflared doesn't run on this system* | Unusual CPU or a 32-bit userland on a 64-bit kernel. Install cloudflared from your distribution or [Cloudflare's packages](https://pkg.cloudflare.com/), then run the installer with `--skip-cloudflared`. |
| *cloudflared ... is managed by your package manager* | Update it with that package manager (`sudo apt upgrade cloudflared`, `sudo dnf upgrade cloudflared`). |
| macOS asks for the Command Line Tools | Accept, wait for the install to finish, run the installer again. |
| Anything else | Run again with `--verbose` to see every command's output. |

## Cloudflare login

| Symptom | Cause and fix |
| --- | --- |
| The login link never completes | Open the link in a browser where you are signed in to the right Cloudflare account and click the domain. The command waits until Cloudflare confirms; a stale link can be retried with `cloudify account connect example.com`. A login that isn't completed within 15 minutes is stopped, so another one can start. Details are in `~/.cloudflared/cloudify/login.log`. |
| The domain isn't listed on the Cloudflare page | The domain isn't in that Cloudflare account, or it isn't active yet (nameservers not switched to Cloudflare). |
| `cloudify account list` shows *(unknown)* as domain | A connection made by an older version: `cloudify account domain default example.com`. |

## Tunnels and routes

| Symptom | Cause and fix |
| --- | --- |
| Browser shows **Cloudflare error 1033** | The tunnel is not connected: start it (`cloudify tunnel start <name>`) and check its log. |
| A tunnel of a **connected domain** never starts (dashboard: stopped, Cloudflare: inactive) and its log (`~/.cloudflared/logs/<name>.log`) says `tunnel credentials file not found` | Fixed in this version. cloudflared had saved the tunnel's credentials next to the domain's certificate in `~/.cloudflared/cloudify/certs/`, where `tunnel run` doesn't look. Cloudify now moves them to `~/.cloudflared`, also for existing tunnels, the next time they start. With an older version, move them yourself and start the tunnel: `mv ~/.cloudflared/cloudify/certs/<tunnel-id>.json ~/.cloudflared/`. In Docker, run `docker exec cloudify sh -c 'mv /root/.cloudflared/cloudify/certs/*-*-*-*-*.json /root/.cloudflared/'`. |
| **502 Bad Gateway** from Cloudflare | The tunnel is connected but your service isn't reachable from where cloudflared runs: is it running, on that port? In Docker with the included compose file (host networking), `localhost` is the server. Check that the service answers there, e.g. `curl http://localhost:PORT` on the server; a container must publish its port. With bridge networking, `localhost` is the Cloudify container itself; use `host.docker.internal` or the container's name instead ([routing to services](docker.md#routing-to-your-services)). |
| **404** for a hostname | The hostname has a DNS record but no route on this tunnel (e.g. removed route, wrong tunnel): `cloudify route list -t <name>`. |
| `DNS_PROBE_FINISHED_NXDOMAIN` / hostname doesn't resolve | The DNS record is missing: `cloudify route check -t <name>`, then `cloudify route repair <hostname> -t <name>`. New records can take a minute to appear. |
| *An A, AAAA, or CNAME record with that host already exists* | Another record uses the hostname. Re-point it with `cloudify route repair <hostname> -t <name> --overwrite` (or delete it in Cloudflare first). |
| Old hostnames still show Cloudflare's error page | DNS records stay after removing routes unless DNS cleanup is on ([API token](getting-started.md#dns-records-and-the-api-token)). Delete them in the Cloudflare dashboard. |
| `tunnel list` says *No (deleted)* under *Exists* | The tunnel was deleted in Cloudflare (or belongs to a domain that isn't connected here). Delete it in Cloudify too, or reconnect the domain. |
| Tunnel doesn't come back after reboot | Check `cloudify tunnel autostart <name> --status`. On Linux, `loginctl show-user $USER -p Linger` must say `yes`; on macOS tunnels start at login; in Docker the container needs `restart: unless-stopped` and Docker must start at boot. |
| *Tunnel is a replica* when editing routes | Routes of a replica are managed on the machine it was exported from. Change them there, export again, import here. |
| Deleting a tunnel is refused because replicas are attached | Remove the replicas on the other machines first (Redundancy page), then delete. |

## Dashboard

| Symptom | Cause and fix |
| --- | --- |
| Can't open it from another computer | By default it only listens on its own machine (`127.0.0.1`). Use an SSH tunnel (`ssh -L 8765:localhost:8765 user@server`), or start it with `--host 0.0.0.0` and HTTPS (or `--insecure-http`) and open the firewall (`sudo ufw allow 8765/tcp`); in Docker change the `ports:` mapping. See [Security](security.md). |
| The setup page asks for a **setup code** | `cloudify serve` prints it when it starts while no account exists (`🔑 Setup code: ...`). Look in its terminal, `journalctl --user -u cloudify-dashboard` (service) or `docker logs cloudify`. Restarting the dashboard prints it again. |
| *Refusing to serve plain HTTP on 0.0.0.0* | Add `--ssl-certfile`/`--ssl-keyfile`, or `--insecure-http` if a TLS proxy is in front or the network is trusted. |
| *This dashboard can't be opened as ...* | DNS-rebinding protection: open it by IP address or `localhost`, or allow the name with `--allowed-host NAME`. |
| *Address already in use* | Another program uses port 8765: `cloudify serve --serve-port 9000`. |
| Signed out unexpectedly | Sessions end after 7 days, when the password is changed elsewhere, and when the dashboard restarts. |
| Forgot the password | See [Files and data → Resetting things](files-and-data.md#resetting-things). |
| Page looks broken after an update | Hard-reload the page (Ctrl+Shift+R / Cmd+Shift+R) so the browser loads the new files. |

## Temporary tunnels and SSH sharing

| Symptom | Cause and fix |
| --- | --- |
| `cloudify -p` prints no URL | The machine can't reach Cloudflare, or TryCloudflare is temporarily unavailable. Try again; check outbound HTTPS. |
| The temporary URL shows error 1033 after a while | The process was stopped (closed terminal, Ctrl+C); quick tunnels end with it. |
| SSH sharing problems | See [SSH sharing → Troubleshooting](ssh.md#troubleshooting). |
| Is anything still reachable from the Internet? | Run the [safety check](security.md#safety-check). It lists every temporary tunnel, SSH sharing session and persistent tunnel, and `--kill` stops the temporary ones. |

## Networks with restrictions

cloudflared connects outbound to Cloudflare on port **7844** (QUIC over UDP, falling back to HTTP/2 over TCP). If a
corporate firewall blocks it, ask for outbound 7844 UDP/TCP to Cloudflare to be allowed. Behind an HTTP proxy, set
`HTTPS_PROXY` for the dashboard's update checks; tunnel traffic itself does not use HTTP proxies.

## Still stuck?

Open an issue with: the output of `cloudify --version`, `cloudflared --version`, your OS, the exact command, and
the error. Remove hostnames, tokens and IDs you don't want to share.
