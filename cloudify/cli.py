"""`cloudify` command line."""

import os
import signal
import sys
import time

import click

from cloudify import __version__
from cloudify.core import cloudflared
from cloudify.core.login import is_logged_in, run_login
from cloudify.core.manager import TunnelManager


class FriendlyGroup(click.Group):
    """Show validation problems (e.g. an invalid tunnel name) as a clean error."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ValueError as e:
            raise click.ClickException(str(e))

    def parse_args(self, ctx, args):
        # `cloudify -ssh host --once` == `cloudify ssh host --once` (options/commands pass through)
        if args and (args[0] in ("-ssh", "--ssh") or args[0].startswith(("-ssh=", "--ssh="))):
            if "=" in args[0]:
                mode, rest = args[0].split("=", 1)[1], args[1:]
            elif len(args) > 1:
                mode, rest = args[1], args[2:]
            else:
                mode, rest = None, []  # click reports the missing value
            if mode in ("host", "remote"):
                args = ["ssh", mode] + list(rest)
        return super().parse_args(ctx, args)


def _resolve_tunnel(name, prompt=False):
    """Use the given tunnel, else the current one, else ask/stop."""
    if name:
        return name
    current = TunnelManager().get_current_tunnel()
    if current:
        return current
    if prompt:
        return click.prompt("Enter tunnel name")
    click.echo("No tunnel specified and no current tunnel selected")
    sys.exit(1)


def _run_temp_tunnel(port, subdomain):
    manager = TunnelManager()
    url = manager.create_temp_tunnel(port, subdomain)
    if not url:
        click.echo("Failed to create temporary tunnel")
        sys.exit(1)
    click.echo(f"Temporary tunnel active: {url}")
    click.echo("Press Ctrl+C to stop")

    def stop(signum, frame):  # `kill`, a closed terminal or a service manager: stop the public URL too
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        click.echo("\nStopping temporary tunnel...")
    finally:
        manager.stop_temp_tunnel(url)    # only this one, not other terminals' tunnels
        manager.stop_own_temp_tunnels()  # and its process even if its record is gone


@click.group(cls=FriendlyGroup, invoke_without_command=True)
@click.version_option(version=__version__)
@click.option('--port', '-p', type=int, help='Create temporary tunnel for localhost:port')
@click.option('--subdomain', help='(ignored) quick tunnels always get a random address')
@click.option('-ssh', '--ssh', 'ssh_mode', type=click.Choice(['host', 'remote']),
              help='Share this machine over SSH (host) or connect to one (remote)')
@click.pass_context
def cli(ctx, port, subdomain, ssh_mode):
    """Cloudflare Tunnel Management CLI Tool"""
    if ssh_mode and ctx.invoked_subcommand is None:
        from cloudify.ssh.cli import run_flag
        run_flag(ctx, ssh_mode)
    elif port and ctx.invoked_subcommand is None:
        _run_temp_tunnel(port, subdomain)
    elif ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# --------------------------------------------------------------------------- #
# Tunnels
# --------------------------------------------------------------------------- #

@cli.group()
def tunnel():
    """Tunnel management commands"""


def _account_id(value):
    """Accept an account ID or one of its domains."""
    if not value:
        return None
    from cloudify.core.accounts import AccountStore, domain_matches
    store = AccountStore()
    if value in store.account_ids():
        return value
    match = next((c for c in store.list() if domain_matches(value, c["domain"])), None)
    if not match:
        raise click.ClickException(f"No connected account or domain matches '{value}' (see `cloudify account list`)")
    return match["account_id"]


@tunnel.command('create')
@click.argument('name', required=False)
@click.option('--no-autostart', is_flag=True, help='Skip setting up auto-start')
@click.option('--account', '-a', help='Cloudflare account ID or one of its domains (if several are connected)')
def create_tunnel(name, no_autostart, account):
    """Create a new tunnel"""
    name = name or click.prompt("Enter tunnel name")
    manager = TunnelManager(name, auto_start_on_create=not no_autostart)
    if not manager.create_tunnel(name, setup_auto_start=not no_autostart, account_id=_account_id(account)):
        click.echo(f"Failed to create tunnel '{name}'")
        sys.exit(1)
    click.echo(f"Tunnel '{name}' ready")


@tunnel.command('list')
def list_tunnels():
    """List all managed tunnels"""
    TunnelManager().list_all_tunnels()


@tunnel.command('status')
@click.argument('name', required=False)
def tunnel_status(name):
    """Show tunnel status"""
    manager = TunnelManager()
    name = name or manager.get_current_tunnel()
    if name:
        if manager.status(name) is False:
            sys.exit(1)
    else:
        click.echo("No tunnel specified and no current tunnel selected")
        manager.list_all_tunnels()


@tunnel.command('start')
@click.argument('name', required=False)
def start_tunnel(name):
    """Start a tunnel"""
    name = _resolve_tunnel(name)
    if not TunnelManager(name).start_tunnel(name):
        sys.exit(1)


@tunnel.command('stop')
@click.argument('name', required=False)
def stop_tunnel(name):
    """Stop a tunnel"""
    name = _resolve_tunnel(name)
    if not TunnelManager(name).stop_tunnel(name):
        sys.exit(1)


@tunnel.command('resume')
def resume_tunnels():
    """Start all tunnels that have auto-start enabled (used at container start)"""
    started = TunnelManager().resume_autostart_tunnels()
    click.echo(f"Started {len(started)} auto-start tunnel(s)" + (f": {', '.join(started)}" if started else ""))


@tunnel.command('delete')
@click.argument('name', required=False)
@click.option('--force', is_flag=True, help='Skip confirmation prompt')
def delete_tunnel(name, force):
    """Delete a tunnel"""
    name = _resolve_tunnel(name)
    if not TunnelManager(name).delete_tunnel(name, force=force):
        click.echo(f"Failed to delete tunnel '{name}'")
        sys.exit(1)


@tunnel.command('adopt')
@click.argument('name')
@click.option('--account', '-a', help='Cloudflare account ID or one of its domains (if several have this name)')
def adopt_tunnel(name, account):
    """Manage an existing tunnel created outside Cloudify"""
    if not TunnelManager().adopt_tunnel(name, account_id=_account_id(account)):
        click.echo(f"Failed to adopt tunnel '{name}'")
        sys.exit(1)


@tunnel.command('use')
@click.argument('name')
def use_tunnel(name):
    """Switch the current tunnel"""
    if not TunnelManager().switch_tunnel(name):
        sys.exit(1)


@tunnel.command('autostart')
@click.argument('name', required=False)
@click.option('--enable', 'action', flag_value='enable', help='Enable auto-start')
@click.option('--disable', 'action', flag_value='disable', help='Disable auto-start')
@click.option('--status', 'action', flag_value='status', help='Check auto-start status')
def autostart_tunnel(name, action):
    """Manage tunnel auto-start"""
    name = _resolve_tunnel(name)
    manager = TunnelManager(name)
    if action == 'enable':
        if not manager.setup_autostart():
            click.echo(f"Failed to enable auto-start for tunnel '{name}'")
            sys.exit(1)
        click.echo(f"Auto-start enabled for tunnel '{name}'")
    elif action == 'disable':
        if not manager.remove_autostart():
            click.echo(f"Failed to disable auto-start for tunnel '{name}'")
            sys.exit(1)
        click.echo(f"Auto-start disabled for tunnel '{name}'")
    else:
        manager.check_autostart_status(verbose=True)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@cli.group()
def route():
    """Route management commands"""


@route.command('add')
@click.argument('domain')
@click.argument('port', type=int)
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
@click.option('--service', default='localhost', help='Service host (default: localhost)')
def add_route(domain, port, tunnel, service):
    """Route DOMAIN to SERVICE:PORT"""
    tunnel = _resolve_tunnel(tunnel, prompt=True)
    if not TunnelManager(tunnel).add_route(domain, port, service, tunnel):
        click.echo("Failed to add route")
        sys.exit(1)


@route.command('edit')
@click.argument('domain')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
@click.option('--hostname', 'new_domain', help='New public hostname')
@click.option('--service', help='New service host (e.g. localhost or a container name)')
@click.option('--port', type=int, help='New service port')
def edit_route(domain, tunnel, new_domain, service, port):
    """Change a route's hostname, service host or port"""
    if not (new_domain or service or port):
        raise click.UsageError("Give at least one of --hostname, --service, --port")
    tunnel = _resolve_tunnel(tunnel, prompt=True)
    if not TunnelManager(tunnel).edit_route(tunnel, domain, new_domain, service, port):
        sys.exit(1)


@route.command('check')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
def check_routes(tunnel):
    """Check that each route's DNS record exists and points at the tunnel"""
    tunnel = _resolve_tunnel(tunnel)
    labels = {"ok": "✓ ok", "missing": "✗ MISSING", "other-target": "⚠ points elsewhere",
              "resolves": "✓ resolves", "unknown": "? unknown"}
    problems = 0
    for record in TunnelManager(tunnel).check_dns(tunnel):
        problems += record["status"] in ("missing", "other-target")
        click.echo(f"{labels.get(record['status'], record['status']):<20} {record['hostname']}"
                   + ("   (no API token: public lookup)" if record["method"] == "dns" else ""))
    if problems:
        click.echo("Fix with: cloudify route repair <hostname> [--overwrite]")
        sys.exit(1)


@route.command('repair')
@click.argument('domain')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
@click.option('--overwrite', is_flag=True, help='Replace a record that points somewhere else')
def repair_route(domain, tunnel, overwrite):
    """Recreate a route's DNS record (e.g. after it was deleted in Cloudflare)"""
    tunnel = _resolve_tunnel(tunnel, prompt=True)
    if not TunnelManager(tunnel).repair_dns(tunnel, domain, overwrite):
        sys.exit(1)


@route.command('remove')
@click.argument('domain')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
def remove_route(domain, tunnel):
    """Remove the route for DOMAIN"""
    tunnel = _resolve_tunnel(tunnel, prompt=True)
    if not TunnelManager(tunnel).remove_route(domain, tunnel):
        click.echo(f"Failed to remove route for {domain}")
        sys.exit(1)


@route.command('list')
@click.option('--tunnel', '-t', help='Tunnel name (uses current if not specified)')
def list_routes(tunnel):
    """List routes for a tunnel"""
    tunnel = _resolve_tunnel(tunnel)
    TunnelManager(tunnel).show_routes(tunnel)


# --------------------------------------------------------------------------- #
# Temporary tunnels
# --------------------------------------------------------------------------- #

@cli.group()
def temp():
    """Temporary tunnel commands"""


@temp.command('create')
@click.argument('port', type=int)
@click.option('--subdomain', help='(ignored) quick tunnels always get a random address')
def create_temp(port, subdomain):
    """Create a temporary tunnel for localhost:PORT"""
    _run_temp_tunnel(port, subdomain)


@temp.command('list')
def list_temp():
    """List active temporary tunnels"""
    TunnelManager().list_temp_tunnels()


@temp.command('stop')
@click.argument('url', required=False)
def stop_temp(url):
    """Stop a temporary tunnel (all if no URL given)"""
    if not TunnelManager().stop_temp_tunnel(url) and url:
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Server, setup, cloudflared
# --------------------------------------------------------------------------- #

def _is_loopback(host: str) -> bool:
    import ipaddress
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@cli.command()
@click.option('--serve-port', default=8765, show_default=True, help='Port for the web UI and API')
@click.option('--host', '-h', default='127.0.0.1', show_default=True,
              help='Address to listen on; 0.0.0.0 = every network interface (needs TLS or --insecure-http)')
@click.option('--ssl-certfile', type=click.Path(exists=True, dir_okay=False), help='TLS certificate (PEM): serve HTTPS')
@click.option('--ssl-keyfile', type=click.Path(exists=True, dir_okay=False), help='TLS private key (PEM)')
@click.option('--insecure-http', is_flag=True,
              help='Allow plain HTTP on a network address (a TLS proxy in front, or a trusted network)')
@click.option('--allowed-host', multiple=True,
              help='Extra host name the dashboard may be opened under (repeatable), e.g. dash.example.com')
@click.option('--api-docs', is_flag=True, help='Serve the interactive API documentation at /api/docs')
@click.option('--api-host', default=None, hidden=True)
@click.option('--windows', is_flag=True, hidden=True)          # kept for old scripts; no longer needed
@click.option('--next-frontend', is_flag=True, hidden=True)    # removed Next.js UI
@click.option('--frontend-port', '-f', default=None, hidden=True)
@click.option('--frontend-host', default=None, hidden=True)
@click.option('--skip-install', is_flag=True, hidden=True)
def serve(serve_port, host, ssl_certfile, ssl_keyfile, insecure_http, allowed_host, api_docs, api_host, windows,
          next_frontend, frontend_port, frontend_host, skip_install):
    """Start the web UI (http://localhost:8765)"""
    import uvicorn
    from cloudify.server.app import app, configure, set_server

    if next_frontend:
        click.echo("Note: the separate Next.js frontend was replaced by the built-in UI.")
    host = api_host or host
    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise click.UsageError("--ssl-certfile and --ssl-keyfile must be given together")
    tls = bool(ssl_certfile)
    insecure_http = insecure_http or os.environ.get("CLOUDIFY_INSECURE_HTTP") == "1"
    if not _is_loopback(host) and not tls and not insecure_http:
        raise click.ClickException(
            f"Refusing to serve plain HTTP on {host}: passwords, login tokens, API tokens and replica files would "
            "cross the network unencrypted. Use --ssl-certfile/--ssl-keyfile, or --insecure-http if a TLS proxy "
            "is in front (or you accept that on a trusted network), or keep the default 127.0.0.1 and use an "
            "SSH tunnel: ssh -L 8765:localhost:8765 <this machine>")
    extra = [h for h in os.environ.get("CLOUDIFY_ALLOWED_HOSTS", "").split(",") if h.strip()]
    configure(allowed_hosts=list(allowed_host) + extra,
              api_docs=api_docs or os.environ.get("CLOUDIFY_API_DOCS") == "1")
    from cloudify.server import auth
    try:
        first_run = auth.users.count() == 0
    except auth.UserStoreError as e:
        first_run = False
        click.echo(f"⚠️  {e}")
    if first_run:
        click.echo(f"🔑 Setup code: {auth.setup_code()}  (the dashboard asks for it to create the first account)")
    scheme = "https" if tls else "http"
    where = "localhost" if _is_loopback(host) or host in ("0.0.0.0", "::") else host
    click.echo(f"🚀 Cloudify {__version__} running at {scheme}://{where}:{serve_port}"
               + ("  (listening on every network interface)" if host in ("0.0.0.0", "::") else ""))
    if not _is_loopback(host) and not tls:
        click.echo("⚠️  Plain HTTP on the network: use it only behind a TLS proxy or on a network you trust.")
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=serve_port, log_level="warning",
                                           ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile,
                                           limit_concurrency=64))  # busy -> 503 instead of piling up work
    set_server(server)
    server.run()
    click.echo("🛑 Server stopped")


@cli.command()
@click.option('--domain', help='Domain you will authorize on the Cloudflare page (e.g. example.com)')
def setup(domain):
    """Initial setup: log in to Cloudflare"""
    click.echo("🔧 Starting initial setup...")
    if not cloudflared.find_binary():
        click.echo("❌ cloudflared is not installed. See https://developers.cloudflare.com/"
                   "cloudflare-one/connections/connect-networks/downloads/")
        sys.exit(1)

    click.echo("🔐 Starting login process...")
    if is_logged_in() and not domain:
        click.echo("✅ Already logged in to Cloudflare (add domains with `cloudify account connect <domain>`)")
    elif run_login(domain) != 0:
        click.echo("❌ Login failed")
        sys.exit(1)
    else:
        click.echo("✅ Login completed successfully!")

    if click.confirm("Would you like to create a tunnel now?"):
        name = click.prompt("Enter tunnel name", default="Universal-tunnel")
        manager = TunnelManager(name, auto_start_on_create=True)
        click.echo(f"✅ Tunnel '{name}' ready" if manager.create_tunnel(name)
                   else f"❌ Failed to create tunnel '{name}'")


@cli.command()
@click.argument('tunnel_name', required=False)
def quickstart(tunnel_name):
    """Quick setup wizard for a new tunnel"""
    tunnel_name = tunnel_name or click.prompt("Enter tunnel name", default="Universal-tunnel")
    enable_autostart = click.confirm("Enable auto-start?", default=True)
    TunnelManager(tunnel_name, auto_start_on_create=enable_autostart).quick_setup(tunnel_name, enable_autostart)


# --------------------------------------------------------------------------- #
# Cloudflare accounts & domains
# --------------------------------------------------------------------------- #

@cli.group()
def account():
    """Connected Cloudflare accounts and domains"""


@account.command('list')
def account_list():
    """Show connected domains"""
    from cloudify.core.accounts import AccountStore
    connections = AccountStore().list()
    if not connections:
        click.echo("No Cloudflare account connected. Run `cloudify account connect <domain>`.")
        return
    click.echo(f"{'ID':<10} {'Domain':<32} {'Account'}")
    for c in connections:
        click.echo(f"{c['id']:<10} {c['domain'] or '(unknown, set with `cloudify account domain`)':<32} {c['account_id']}"
                   + ("   DNS cleanup: on" if c.get("api_token") else ""))


@account.command('connect')
@click.argument('domain')
def account_connect(domain):
    """Authorize DOMAIN (can be in another Cloudflare account)"""
    from cloudify.core.validation import is_valid_domain
    domain = domain.strip().lower().strip(".")
    if not is_valid_domain(domain) or domain.startswith("*."):
        raise click.ClickException("Enter a domain like example.com")
    if run_login(domain) != 0:
        click.echo("❌ Login failed")
        sys.exit(1)


@account.command('domain')
@click.argument('connection_id')
@click.argument('domain')
def account_domain(connection_id, domain):
    """Set the domain name of a connection made by an older version"""
    from cloudify.core.accounts import AccountStore
    try:
        AccountStore().set_domain(connection_id, domain)
    except KeyError:
        raise click.ClickException(f"No connection '{connection_id}'")
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo(f"Connection {connection_id} is now {domain}")


@account.command('token')
@click.argument('connection_id')
@click.option('--remove', is_flag=True, help='Forget the token')
def account_token(connection_id, remove):
    """Add a Cloudflare API token (Zone · DNS · Edit) so DNS records are deleted automatically"""
    from cloudify.core import dns_api
    from cloudify.core.accounts import AccountStore
    store = AccountStore()
    connection = store.get(connection_id)
    if not connection:
        raise click.ClickException(f"No connection '{connection_id}'")
    token = None
    if not remove:
        token = click.prompt("API token", hide_input=True).strip()
        try:
            dns_api.verify_token(token, connection["zone_id"])
        except dns_api.DnsApiError as e:
            raise click.ClickException(str(e))
    store.set_api_token(connection_id, token)
    click.echo("API token removed" if remove else f"DNS cleanup enabled for {connection['domain'] or connection_id}")


@account.command('remove')
@click.argument('connection_id')
@click.option('--force', is_flag=True, help="Disconnect even if it is the last domain of an account that tunnels use")
def account_remove(connection_id, force):
    """Disconnect a domain (running tunnels keep running)"""
    from cloudify.core.accounts import AccountStore
    store = AccountStore()
    connection = store.get(connection_id)
    if not connection:
        raise click.ClickException(f"No connection '{connection_id}'")
    others = [c for c in store.list() if c["account_id"] == connection["account_id"] and c["id"] != connection_id]
    tunnels = [n for n, i in TunnelManager().state.tunnels().items()
               if i.get("account_id") == connection["account_id"] and not i.get("replica") and not i.get("temp_tunnel")]
    if not others and tunnels and not force:
        raise click.ClickException(
            f"This is the last domain of its Cloudflare account and {len(tunnels)} tunnel(s) use that account "
            f"({', '.join(tunnels)}). They keep running, but you can't add routes to or delete them until the "
            "account is connected again. Add --force to disconnect anyway.")
    try:
        record = store.remove(connection_id)
    except KeyError:
        raise click.ClickException(f"No connection '{connection_id}'")
    click.echo(f"Disconnected {record['domain'] or connection_id}")


# --------------------------------------------------------------------------- #
# Replicas
# --------------------------------------------------------------------------- #

@cli.group()
def replica():
    """Run tunnels on several machines (automatic failover/load balancing)"""


def _replica(fn, *args):
    from cloudify.core.replica import ReplicaError
    try:
        return fn(*args)
    except ReplicaError as e:
        raise click.ClickException(str(e))


@replica.command('plan')
@click.option('--tunnel', '-t', 'tunnels', multiple=True, help='Whole tunnel (repeatable)')
@click.option('--hostname', '-H', 'hostnames', multiple=True, help='Single hostname (repeatable)')
def replica_plan(tunnels, hostnames):
    """Show what replicating these tunnels/hostnames involves"""
    from cloudify.core import replica as rp
    result = rp.plan(TunnelManager(), list(tunnels), list(hostnames))
    for name in result["whole"]:
        click.echo(f"✓ {name}: whole tunnel can be replicated as is")
    for name, entry in result["partial"].items():
        click.echo(f"• {name}: {', '.join(entry['selected'])} share the tunnel with {', '.join(entry['others'])}.\n"
                   f"  Split them first: cloudify replica split {name} {' '.join(entry['selected'])} --name {name}-ha")
    for hostname in result["unknown"]:
        click.echo(f"✗ {hostname}: not routed by any tunnel on this machine")


@replica.command('split')
@click.argument('tunnel')
@click.argument('hostnames', nargs=-1, required=True)
@click.option('--name', 'new_name', required=True, help='Name of the new tunnel')
def replica_split(tunnel, hostnames, new_name):
    """Move HOSTNAMES of TUNNEL into a new tunnel (re-points their DNS)"""
    from cloudify.core import replica as rp
    if not rp.split(TunnelManager(), tunnel, list(hostnames), new_name):
        sys.exit(1)


@replica.command('export')
@click.argument('output', type=click.Path(dir_okay=False))
@click.option('--tunnel', '-t', 'tunnels', multiple=True, required=True, help='Tunnel to replicate (repeatable)')
@click.password_option('--passphrase', prompt='Passphrase to encrypt the file', help='Encryption passphrase')
def replica_export(output, tunnels, passphrase):
    """Write an encrypted file that makes another machine a replica"""
    import json
    from cloudify.core import replica as rp
    bundle = _replica(rp.export, TunnelManager(), list(tunnels), passphrase)
    with open(output, "w") as f:
        json.dump(bundle, f, indent=2)
    os_chmod_private(output)
    click.echo(f"Wrote {output}. On the other machine run: cloudify replica import {os.path.basename(output)}")


@replica.command('import')
@click.argument('bundle_file', type=click.Path(exists=True, dir_okay=False))
@click.option('--passphrase', prompt=True, hide_input=True, help='Passphrase used when exporting')
@click.option('--no-autostart', is_flag=True, help="Don't start the replica on boot")
def replica_import(bundle_file, passphrase, no_autostart):
    """Run the tunnels from an exported file on this machine as replicas"""
    import json
    from cloudify.core import replica as rp
    try:
        with open(bundle_file) as f:
            bundle = json.load(f)
    except ValueError:
        raise click.ClickException("That file is not a Cloudify replica file")
    _replica(rp.import_bundle, TunnelManager(), bundle, passphrase, not no_autostart)


def os_chmod_private(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


from cloudify.ssh.cli import ssh_group  # noqa: E402  (separate feature package)

cli.add_command(ssh_group)


@cli.group('cloudflared')
def cloudflared_group():
    """Check or update the installed cloudflared binary"""


@cloudflared_group.command('version')
def cloudflared_version():
    """Show installed and latest cloudflared versions"""
    installed, latest = cloudflared.installed_version(), cloudflared.latest_version()
    path = cloudflared.find_binary()
    click.echo(f"Installed: {installed or 'not installed'}" + (f" ({path})" if path else ""))
    click.echo(f"Latest:    {latest or 'unknown (could not reach GitHub)'}")
    if cloudflared.update_available(installed, latest):
        click.echo("An update is available: run `cloudify cloudflared update`")


@cloudflared_group.command('update')
def cloudflared_update():
    """Update cloudflared to the latest release"""
    installed, latest = cloudflared.installed_version(), cloudflared.latest_version()
    if not installed:
        click.echo("cloudflared is not installed")
        sys.exit(1)
    if latest and not cloudflared.update_available(installed, latest):
        click.echo(f"cloudflared {installed} is already the latest version")
        return
    click.echo(f"Updating cloudflared {installed} -> {latest or 'latest'}...")
    ok, message = cloudflared.self_update()
    click.echo(message)
    if not ok:
        sys.exit(1)
    click.echo("Restart running tunnels to use the new version "
               "(cloudify tunnel stop <name> && cloudify tunnel start <name>)")


if __name__ == "__main__":
    cli()
