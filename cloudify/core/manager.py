"""TunnelManager: create tunnels, add/remove routes, start/stop, auto-start.

Used by both the CLI (interactive, prints progress) and the web server
(non-interactive, collects messages so errors can be shown in the UI).
"""

import hashlib
import json
import os
import re
import shutil
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import yaml

from cloudify.core import autostart, cloudflared, dns_api, paths, processes, quick_tunnel
from cloudify.core.accounts import AccountStore
from cloudify.core.state import StateStore
from cloudify.core.validation import (
    NEW_TUNNEL_NAME_HELP,
    is_safe_tunnel_name,
    is_valid_domain,
    is_valid_service_host,
    is_valid_tunnel_name,
    parse_port,
    strip_scheme,
)


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _wait_or_kill(process, timeout: float = 10):
    """cloudflared shuts down gracefully on SIGTERM; don't wait forever for it."""
    try:
        process.wait(timeout=timeout)
    except Exception:  # subprocess.TimeoutExpired
        process.kill()
        process.wait(timeout=5)


class AccountNotConnected(ValueError):
    """The tunnel's Cloudflare account is known but none of its domains is connected here."""


class ConfigError(ValueError):
    """A tunnel's YAML configuration can't be read; nothing is changed."""


def _is_catch_all(rule) -> bool:
    """An ingress rule that matches every request (cloudflared requires it to be last)."""
    return isinstance(rule, dict) and not rule.get("path") and rule.get("hostname") in (None, "", "*")


def _same_host(a, b) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


class TunnelManager:
    def __init__(self, tunnel_name: Optional[str] = None, auto_start_on_create: bool = True,
                 interactive: bool = True, out: Optional[Callable[[str], None]] = None):
        """
        Args:
            tunnel_name: tunnel to work on (nothing is recorded until an operation succeeds)
            auto_start_on_create: offer/enable auto-start when creating a tunnel
            interactive: ask questions on stdin (CLI). The web server passes False.
            out: where progress messages go (default: print)
        """
        if tunnel_name and not is_safe_tunnel_name(tunnel_name):
            raise ValueError(f"Invalid tunnel name '{tunnel_name}'")

        self.interactive = interactive
        self.auto_start_on_create = auto_start_on_create
        self.messages: List[str] = []
        self.dns_notes: List[Dict] = []  # what happened to DNS records in this operation
        self._print = out or print
        self.state = StateStore()
        self.accounts = AccountStore()
        self.config_dir = paths.config_dir()
        self._temp_processes = {}

        self.tunnel_name = tunnel_name
        self.config_file = paths.tunnel_config(tunnel_name) if tunnel_name else None
        self._lookup_failed: set = set()  # accounts whose tunnel list couldn't be read

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def out(self, message: str = ""):
        self.messages.append(message)
        self._print(message)

    def last_error(self, default: str = "Operation failed") -> str:
        """Most recent meaningful message (used as the API error detail)."""
        for message in reversed(self.messages):
            text = message.strip().strip("=").strip()
            if text:
                return text
        return default

    def _ask(self, prompt: str, default: bool = True) -> bool:
        """Yes/no question on the CLI; non-interactive callers get `default`.
        On the CLI only an explicit "y"/"yes" counts as yes."""
        if not self.interactive:
            return default
        try:
            return input(prompt).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def _backend(self) -> Optional[autostart.AutostartBackend]:
        return autostart.get_backend(self.out)

    def _select(self, tunnel_name: str):
        self.tunnel_name = tunnel_name
        self.config_file = paths.tunnel_config(tunnel_name)

    @staticmethod
    def _read_yaml(path: Optional[str]) -> Tuple[Dict, Optional[str]]:
        """(config, error): a missing file is an empty config, a broken one an error."""
        if not path:
            return {}, None
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}, None
        except (OSError, yaml.YAMLError) as e:
            return {}, f"{path} can't be read ({str(e).splitlines()[0] if str(e) else type(e).__name__})"
        if not isinstance(data, dict):
            return {}, f"{path} is not a cloudflared configuration"
        return data, None

    def _load_config(self, tunnel_name: Optional[str] = None, strict: bool = False) -> Dict:
        """The tunnel's config. A broken file reads as empty, or raises ConfigError when
        `strict` (anything that writes the file back must not start from an empty one)."""
        path = paths.tunnel_config(tunnel_name) if tunnel_name else self.config_file
        data, error = self._read_yaml(path)
        if error and strict:
            raise ConfigError(f"{error}. Fix or remove the file first.")
        return data

    def _save_config(self, config: Dict, tunnel_name: Optional[str] = None):
        path = paths.tunnel_config(tunnel_name) if tunnel_name else self.config_file
        with open(path, "w") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    @staticmethod
    def _routes_from_config(config: Dict) -> List[Dict]:
        return [{"domain": rule["hostname"], "service": rule.get("service", "")}
                for rule in (config.get("ingress") or [])
                if isinstance(rule, dict) and "hostname" in rule]

    def config_hash(self, tunnel_name: str) -> str:
        """Fingerprint of a tunnel's routes; replicas must have the same one."""
        ingress = self._load_config(tunnel_name).get("ingress") or []
        return hashlib.sha256(json.dumps(ingress, sort_keys=True).encode()).hexdigest()[:16]

    # ------------------------------------------------------------------ #
    # Cloudflare accounts
    # ------------------------------------------------------------------ #

    def remote_tunnels(self) -> List[Dict]:
        """Tunnels in every connected Cloudflare account (tagged with account_id).

        Accounts whose list couldn't be read (offline, expired certificate) are
        remembered in self._lookup_failed, so callers don't mistake them for
        accounts without tunnels."""
        found, seen = [], set()
        self._lookup_failed = set()
        account_ids = self.accounts.account_ids() or [None]
        for account_id in account_ids:
            tunnels = cloudflared.list_tunnels_checked(self.accounts.cert_for_account(account_id))
            if tunnels is None:
                self._lookup_failed.add(account_id)
                continue
            for tunnel in tunnels:
                if tunnel.get("id") not in seen:
                    seen.add(tunnel.get("id"))
                    found.append({**tunnel, "account_id": account_id})
        return found

    def _pick_remote(self, tunnel_name: str, candidates: List[Dict], account_id: Optional[str] = None
                     ) -> Tuple[Optional[Dict], Optional[str]]:
        """Which of the Cloudflare tunnels called `tunnel_name` is ours: (tunnel, error).

        Names are only unique within one Cloudflare account, so the recorded tunnel ID
        wins, then the recorded (or requested) account. A tunnel recorded by an older
        version without either belongs to the account of ~/.cloudflared/cert.pem, the
        only account those versions knew. Anything else that matches several accounts
        is ambiguous and must not be guessed."""
        named = [t for t in candidates if t.get("name") == tunnel_name]
        info = self.state.get(tunnel_name)
        recorded = self._recorded_id(tunnel_name)
        if recorded:
            return next((t for t in named if str(t.get("id", "")).lower() == recorded), None), None
        account = account_id or info.get("account_id")
        if account:
            return next((t for t in named if t.get("account_id") == account), None), None
        if self._lookup_failed:  # a missing name may just be in the account that couldn't be asked
            return None, ("Cloudflare couldn't be asked for the tunnels of every connected account, so "
                          f"'{tunnel_name}' isn't matched by name now. Try again.")
        if len(named) <= 1:
            return (named[0] if named else None), None
        default_account = self.accounts.default_account_id()
        if tunnel_name in self.state.tunnels() and default_account:
            match = [t for t in named if t.get("account_id") == default_account]
            if len(match) == 1:
                return match[0], None
        accounts = ", ".join(sorted(str(t.get("account_id")) for t in named))
        return None, (f"Several connected Cloudflare accounts have a tunnel named '{tunnel_name}' ({accounts}). "
                      "Choose one with --account (an account ID or one of its domains).")

    def _recorded_id(self, tunnel_name: str) -> Optional[str]:
        """The tunnel UUID this machine recorded for a managed name: from the state, else from the
        tunnel's own config (`tunnel:`), which older versions wrote without recording the ID."""
        if tunnel_name not in self.state.tunnels():
            return None
        info = self.state.get(tunnel_name)
        if info.get("tunnel_id"):
            return str(info["tunnel_id"]).lower()
        config, _ = self._read_yaml(paths.tunnel_config(tunnel_name))
        value = str(config.get("tunnel") or "")
        return value.lower() if _UUID.fullmatch(value) else None

    def _resolve_remote(self, tunnel_name: str, account_id: Optional[str] = None
                        ) -> Tuple[Optional[Dict], Optional[str]]:
        return self._pick_remote(tunnel_name, self.remote_tunnels(), account_id)

    def account_of(self, tunnel_name: str) -> Optional[str]:
        """Account the tunnel belongs to (looked up once for tunnels from older versions)."""
        info = self.state.get(tunnel_name)
        if info.get("account_id"):
            return info["account_id"]
        remote, _ = self._resolve_remote(tunnel_name)
        if remote and remote.get("account_id") and tunnel_name in self.state.tunnels():
            self.state.update(tunnel_name, {"account_id": remote["account_id"], "tunnel_id": remote.get("id")})
        return remote.get("account_id") if remote else None

    def _cert_for_tunnel(self, tunnel_name: str) -> Optional[str]:
        """Certificate of the tunnel's Cloudflare account (None: cloudflared's default, only used when
        the account is unknown). Raises AccountNotConnected when the account is known but not connected
        here: cloudflared would otherwise use ~/.cloudflared/cert.pem, i.e. another account, and find or
        change that account's tunnel of the same name."""
        account = self.account_of(tunnel_name)
        if account and account not in self.accounts.account_ids():
            raise AccountNotConnected(
                f"The Cloudflare account of '{tunnel_name}' ({account}) is not connected on this machine. "
                "Connect one of its domains first (cloudify account connect <domain>).")
        return self.accounts.cert_for_account(account)

    def _tunnel_cert_or_error(self, tunnel_name: str) -> Tuple[bool, Optional[str]]:
        """(ok, cert) for commands that change the tunnel in Cloudflare."""
        try:
            return True, self._cert_for_tunnel(tunnel_name)
        except AccountNotConnected as e:
            self.out(str(e))
            return False, None

    def is_replica(self, tunnel_name: str) -> bool:
        return bool(self.state.get(tunnel_name).get("replica"))

    def _refuse_on_replica(self, tunnel_name: str) -> bool:
        replica = self.state.get(tunnel_name).get("replica")
        if replica:
            self.out(f"'{tunnel_name}' is a replica of {replica.get('source') or 'another machine'}. "
                     "Change its routes on that machine and export it again.")
            return True
        return False

    def _validate_route(self, domain: str, host: str, port) -> Optional[Tuple[str, str, int]]:
        domain = strip_scheme(domain).lower()
        if not is_valid_domain(domain):
            self.out(f"Invalid domain '{domain}'. Use a full hostname like app.example.com")
            return None
        host = strip_scheme(host or "localhost")
        if not is_valid_service_host(host):
            self.out(f"Invalid service host '{host}'")
            return None
        port_number = parse_port(port)
        if port_number is None:
            self.out(f"Invalid port '{port}'")
            return None
        return domain, host, port_number

    def _connection_for(self, hostname: str, tunnel_name: str) -> Tuple[bool, Optional[str]]:
        """(ok, cert) to create DNS for `hostname` on the tunnel's account."""
        if not self.accounts.is_connected():
            return True, None  # cloudflared falls back to its default cert (and reports errors)
        account_id = self.account_of(tunnel_name)
        connection = self.accounts.for_hostname(hostname, account_id)
        if connection:
            return True, connection["cert"]
        domains = self.accounts.domains(account_id)
        self.out(f"{hostname} is not on a domain connected for this tunnel's Cloudflare account"
                 + (f" (connected: {', '.join(domains)})" if domains else "")
                 + ". Connect its domain on the Domains page first.")
        return False, None

    def _tunnel_id(self, tunnel_name: str) -> Optional[str]:
        recorded = self.state.get(tunnel_name).get("tunnel_id")
        if recorded:
            return recorded
        try:
            return cloudflared.tunnel_id(tunnel_name, self._cert_for_tunnel(tunnel_name))
        except AccountNotConnected:
            return None

    def _cleanup_dns(self, hostname: str, tunnel_name: str, tunnel_id: Optional[str]) -> str:
        """Delete hostname's CNAME if it points at this tunnel. Returns the outcome."""
        connection = self.accounts.for_hostname(hostname, self.account_of(tunnel_name))
        outcome, detail = "manual", None
        if not tunnel_id:
            detail = "tunnel ID unknown"
        elif not connection or not connection.get("zone_id"):
            detail = "domain not connected"
        else:
            try:
                outcome = dns_api.delete_tunnel_record(self.accounts.dns_tokens(connection),
                                                       connection["zone_id"], hostname, tunnel_id)
            except dns_api.DnsApiError as e:
                detail = str(e)
        self.dns_notes.append({"hostname": hostname, "outcome": outcome, "detail": detail})
        if outcome == "deleted":
            self.out(f"Deleted the DNS record for {hostname}")
        elif outcome == "missing":
            self.out(f"No DNS record found for {hostname}")
        elif outcome == "other-target":
            self.out(f"The DNS record for {hostname} points somewhere else now; left it unchanged")
        else:
            self.out(f"Delete the DNS record for {hostname} in the Cloudflare dashboard (DNS -> Records)"
                     + ("; add an API token on the Domains page to have Cloudify do it" if connection else "")
                     + (f" [{detail}]" if detail and connection else ""))
        return outcome

    def check_dns(self, tunnel_name: str) -> List[Dict]:
        """Is each route's DNS record still there and pointing at this tunnel?

        status: "ok" | "missing" | "other-target" | "resolves" (exists, target
        unknown without an API token) | "unknown". Detects records deleted by
        accident in the Cloudflare dashboard.
        """
        tunnel_id = self._tunnel_id(tunnel_name)
        account_id = self.account_of(tunnel_name)
        results = []
        for route in self.state.get(tunnel_name).get("routes", []):
            hostname = route["domain"]
            connection = self.accounts.for_hostname(hostname, account_id)
            status, method, detail = "unknown", "dns", None
            tokens = self.accounts.dns_tokens(connection) if connection else []
            if tunnel_id and connection and connection.get("zone_id") and connection.get("api_token"):
                try:
                    status, method = dns_api.record_status(tokens, connection["zone_id"], hostname, tunnel_id), "api"
                except dns_api.DnsApiError as e:
                    detail = str(e)
            if method == "dns":
                status = dns_api.public_lookup(hostname)
            results.append({"hostname": hostname, "status": status, "method": method, "detail": detail})
        return results

    def repair_dns(self, tunnel_name: str, hostname: str, overwrite: bool = False) -> bool:
        """(Re)create the CNAME hostname -> tunnel; overwrite re-points a record aimed elsewhere."""
        if self._refuse_on_replica(tunnel_name):
            return False
        hostname = strip_scheme(hostname).lower()
        if not any(_same_host(r.get("domain"), hostname) for r in self.state.get(tunnel_name).get("routes", [])):
            self.out(f"{hostname} is not a route of '{tunnel_name}'")
            return False
        ok, cert = self._connection_for(hostname, tunnel_name)
        if not ok:
            return False
        args = ["tunnel", "route", "dns"] + (["--overwrite-dns"] if overwrite else []) + [tunnel_name, hostname]
        result = cloudflared.run(args, cert=cert)
        if result.returncode != 0:
            error = cloudflared.output(result)
            if "already exists" in error.lower():
                self.out(f"A different DNS record already exists for {hostname}; re-point it to this tunnel to replace it")
            else:
                self.out(f"Failed to create the DNS record for {hostname}: {error}")
            return False
        self.out(f"DNS record for {hostname} now points at '{tunnel_name}'")
        return True

    def _route_dns(self, hostname: str, tunnel_name: str, cert: Optional[str], overwrite: bool = False) -> bool:
        self.out(f"Creating DNS record for {hostname}...")
        args = ["tunnel", "route", "dns"] + (["--overwrite-dns"] if overwrite else []) + [tunnel_name, hostname]
        result = cloudflared.run(args, cert=cert)
        if result.returncode == 0:
            self.out(f"DNS record created for {hostname}")
            return True
        self.out(f"Failed to create DNS record ({cloudflared.output(result)}). "
                 "You may need to create it manually.")
        return False

    # ------------------------------------------------------------------ #
    # Selection / listing
    # ------------------------------------------------------------------ #

    def use_tunnel(self, tunnel_name: str):
        """Select a tunnel that is being created or adopted and record it. Only the CLI
        changes the "current" tunnel; requests from the web server never do."""
        if not is_safe_tunnel_name(tunnel_name):
            raise ValueError(f"Invalid tunnel name '{tunnel_name}'")
        self._select(tunnel_name)
        self.state.register(tunnel_name, make_current=self.interactive)
        self.out(f"Now managing tunnel: {tunnel_name}")

    def switch_tunnel(self, tunnel_name: str) -> bool:
        """`cloudify tunnel use NAME`: make a managed tunnel the current one."""
        if not is_safe_tunnel_name(tunnel_name):
            self.out(f"Invalid tunnel name '{tunnel_name}'")
            return False
        if tunnel_name not in self.state.tunnels():
            self.out(f"'{tunnel_name}' is not managed by Cloudify. "
                     "Create it (cloudify tunnel create) or adopt it (cloudify tunnel adopt).")
            return False
        self._select(tunnel_name)
        self.state.set_current(tunnel_name)
        self.out(f"Now managing tunnel: {tunnel_name}")
        return True

    def _known(self, tunnel_name: Optional[str]) -> bool:
        """Refuse (with a message) tunnels Cloudify doesn't manage."""
        if not tunnel_name:
            self.out("No tunnel specified")
            return False
        if tunnel_name not in self.state.tunnels():
            self.out(f"Tunnel '{tunnel_name}' is not managed by Cloudify (see cloudify tunnel list)")
            return False
        return True

    def get_current_tunnel(self) -> Optional[str]:
        return self.tunnel_name or self.state.current()

    def tunnel_status(self, tunnel_name: str) -> str:
        """'running (service)', 'running (manual)', 'running (temp)' or 'stopped'."""
        info = self.state.get(tunnel_name)
        if processes.tunnel_running(tunnel_name):
            return "running (service)" if info.get("auto_start") else "running (manual)"
        pid = info.get("temp_process_id")
        if pid:
            if processes.is_quick_tunnel(pid):
                return "running (temp)"
            self.state.update(tunnel_name, {"temp_process_id": None})
        return "stopped"

    _check_tunnel_status = tunnel_status  # backwards compatibility

    def list_all_tunnels(self) -> List[Dict]:
        """Managed tunnels with live status (also printed as a table)."""
        remote = self.remote_tunnels()
        connected = set(self.accounts.account_ids())
        managed = {n: i for n, i in self.state.tunnels().items() if not i.get("temp_tunnel")}
        claimed = set()

        tunnels = []
        for name, info in managed.items():
            actual, _ = self._pick_remote(name, remote)
            if actual:
                claimed.add(actual.get("id"))
                if actual.get("account_id") and not info.get("account_id"):
                    info.update(account_id=actual["account_id"], tunnel_id=actual.get("id"))
                    self.state.update(name, {"account_id": actual["account_id"], "tunnel_id": actual.get("id")})
            replica = info.get("replica")
            exported = info.get("exported")
            config_hash = self.config_hash(name)
            cert = self.accounts.cert_for_account(info.get("account_id")) if actual else None
            account = info.get("account_id")
            if actual is not None or replica:
                exists = True  # replicas usually have no Cloudflare login, so existence can't be checked
            elif (account and account not in connected) or (account in self._lookup_failed) or \
                    (not account and self._lookup_failed):
                exists = None  # unknown: its account isn't connected here, or Cloudflare couldn't be asked
            else:
                exists = False
            tunnels.append({
                "name": name,
                "managed": True,
                "config_file": info.get("config_file", paths.tunnel_config(name)),
                "routes": info.get("routes", []),
                "auto_start": info.get("auto_start", False),
                "created_at": info.get("created_at", "unknown"),
                "exists": exists,
                "id": actual.get("id") if actual else info.get("tunnel_id"),
                "account_id": account,
                # the local process is checked whatever Cloudflare says
                "status": self.tunnel_status(name),
                "replica": replica,
                "exported": exported,
                "replicas_outdated": bool(exported and exported.get("config_hash") != config_hash),
                # Machines running this tunnel (only looked up for tunnels shared with other machines)
                "connectors": cloudflared.connectors(name, cert) if (actual and (exported or replica)) else None,
            })

        self.out("\n=== All Managed Tunnels ===")
        if tunnels:
            self.out(f"\nFound {len(tunnels)} managed tunnel(s):\n")
            self.out(f"{'Name':<20} {'Status':<18} {'Routes':<8} {'Auto-start':<12} {'Exists'}")
            self.out("-" * 75)
            for t in tunnels:
                exists = {True: "Yes", False: "No (deleted)", None: "Unknown"}[t["exists"]]
                self.out(f"{t['name']:<20} {t['status']:<18} {len(t['routes']):<8} "
                         f"{'Yes' if t['auto_start'] else 'No':<12} {exists}")
        else:
            self.out("No managed tunnels found.")

        unmanaged = [t for t in remote if t.get("id") not in claimed]
        self.out("\n=== Other Cloudflare Tunnels ===")
        if unmanaged:
            self.out(f"\nFound {len(unmanaged)} unmanaged tunnel(s):")
            for t in unmanaged:
                self.out(f"  • {t.get('name')} (ID: {t.get('id')})")
            self.out("\nTo manage these tunnels, use: cloudify tunnel adopt <tunnel-name>")
        else:
            self.out("No unmanaged tunnels found.")
        return tunnels

    def unmanaged_tunnels(self) -> List[Dict]:
        """Tunnels in the Cloudflare account that Cloudify does not manage yet."""
        remote = self.remote_tunnels()
        claimed = {t.get("id") for t in (self._pick_remote(n, remote)[0] for n in self.state.tunnels()) if t}
        return [{"name": t.get("name"), "id": t.get("id"), "account_id": t.get("account_id")}
                for t in remote if t.get("id") not in claimed]

    def adopt_tunnel(self, tunnel_name: str, account_id: Optional[str] = None) -> bool:
        """Start managing a tunnel that already exists in the Cloudflare account."""
        if not is_safe_tunnel_name(tunnel_name):
            self.out(f"Invalid tunnel name '{tunnel_name}'")
            return False
        if tunnel_name in self.state.tunnels():
            self.out(f"Tunnel '{tunnel_name}' is already managed")
            return False
        remote, error = self._resolve_remote(tunnel_name, account_id)
        if error:
            self.out(error)
            return False
        if not remote:
            self.out(f"Tunnel '{tunnel_name}' not found")
            return False

        self.use_tunnel(tunnel_name)
        self.state.update(tunnel_name, {"account_id": remote.get("account_id"), "tunnel_id": remote.get("id")})
        # Prefer a tunnel-specific config over the generic config.yml, but only one that
        # belongs to this tunnel: config.yml is often another tunnel's.
        for candidate in (paths.tunnel_config(tunnel_name),
                          os.path.join(self.config_dir, f"{tunnel_name}.yml"),
                          os.path.join(self.config_dir, "config.yml")):
            if not os.path.exists(candidate):
                continue
            config, error = self._read_yaml(candidate)
            if error:
                self.out(f"Skipped {candidate}: {error}")
                continue
            owner = str(config.get("tunnel") or "")
            if owner not in (tunnel_name, remote.get("id")) and \
                    not (owner == "" and candidate == paths.tunnel_config(tunnel_name)):
                continue
            if candidate != self.config_file:
                shutil.copy2(candidate, self.config_file)
            routes = self._routes_from_config(config)
            self.state.update(tunnel_name, {"routes": routes})
            self.out(f"Adopted tunnel '{tunnel_name}' with {len(routes)} routes")
            return True
        self.out(f"Adopted tunnel '{tunnel_name}' (no config found)")
        return True

    # ------------------------------------------------------------------ #
    # Login / create
    # ------------------------------------------------------------------ #

    def login(self, domain: Optional[str] = None) -> bool:
        """Connect a Cloudflare domain (opens the browser authorization link)."""
        from cloudify.core.login import run_login
        self.out("Logging in to Cloudflare...")
        ok = run_login(domain) == 0
        self.out("Login successful!" if ok else "Login failed")
        return ok

    def create_tunnel(self, tunnel_name: Optional[str] = None, setup_auto_start: Optional[bool] = None,
                      account_id: Optional[str] = None) -> bool:
        if not tunnel_name:
            tunnel_name = self.tunnel_name
            if not tunnel_name and self.interactive:
                tunnel_name = input("Enter tunnel name: ").strip()
        if not tunnel_name:
            self.out("Tunnel name required")
            return False
        if not is_safe_tunnel_name(tunnel_name):
            self.out(f"Invalid tunnel name '{tunnel_name}'")
            return False
        if setup_auto_start is None:
            setup_auto_start = self.auto_start_on_create

        account_ids = self.accounts.account_ids()
        if account_id and account_id not in account_ids:
            self.out(f"Unknown Cloudflare account '{account_id}'")
            return False
        remote = None
        if not self.is_replica(tunnel_name):
            remote, error = self._resolve_remote(tunnel_name, account_id)
            if error:
                self.out(error)
                return False
            if remote and account_id and remote.get("account_id") != account_id:
                self.out(f"A tunnel named '{tunnel_name}' is already managed in account {remote.get('account_id')}; "
                         "choose another name")
                return False
            if not remote and account_id and tunnel_name in self.state.tunnels() and \
                    self.state.get(tunnel_name).get("account_id") not in (None, account_id):
                self.out(f"A tunnel named '{tunnel_name}' is already managed in another account; choose another name")
                return False
        exists = remote is not None or self.is_replica(tunnel_name)
        if not exists and not is_valid_tunnel_name(tunnel_name):
            self.out(f"Invalid tunnel name '{tunnel_name}'. {NEW_TUNNEL_NAME_HELP}")
            return False

        cert = None
        if not exists:
            if not account_id and len(account_ids) > 1:
                choices = "; ".join(f"{a} ({', '.join(self.accounts.domains(a)) or 'no domain'})" for a in account_ids)
                self.out(f"Several Cloudflare accounts are connected; choose one: {choices}")
                return False
            account_id = account_id or (account_ids[0] if account_ids else None)
            cert = self.accounts.cert_for_account(account_id)

        was_managed = tunnel_name in self.state.tunnels()
        self.use_tunnel(tunnel_name)
        if remote:
            self.state.update(tunnel_name, {"account_id": remote.get("account_id"), "tunnel_id": remote.get("id")})

        if exists:
            self.out(f"Tunnel '{tunnel_name}' already exists")
            if setup_auto_start and not self.check_autostart_status(verbose=False):
                if self._ask("Auto-start is not configured. Set it up now? (y/n): "):
                    self.setup_autostart()
            return True

        self.out(f"Creating tunnel '{tunnel_name}'...")
        result = cloudflared.run(["tunnel", "create", tunnel_name], cert=cert)
        if result.returncode != 0:
            if not was_managed:  # don't leave a tunnel behind that only exists in Cloudify's list
                self.state.unregister(tunnel_name)
            self.out(f"Failed to create tunnel: {cloudflared.output(result)}")
            return False
        created = next((t for t in (cloudflared.list_tunnels_checked(cert) or []) if t.get("name") == tunnel_name), None)
        self.state.update(tunnel_name, {"account_id": account_id or (created or {}).get("account_id"),
                                        "tunnel_id": (created or {}).get("id")})
        self.out("Tunnel created successfully!")

        if setup_auto_start:
            self.out("\nSetting up auto-start...")
            if self._ask("Enable tunnel to start automatically? (y/n): "):
                self.setup_autostart()
        return True

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #

    def add_route(self, domain: str, localhost_port, end_user_url: str = "localhost",
                  tunnel_name: Optional[str] = None) -> bool:
        """Route https://<domain> to http://<end_user_url>:<port> through the tunnel."""
        tunnel_name = tunnel_name or self.tunnel_name
        if not tunnel_name:
            self.out("No tunnel specified. Use `cloudify tunnel use <name>` first")
            return False

        validated = self._validate_route(domain, end_user_url, localhost_port)
        if not validated:
            return False
        domain, host, port = validated
        if self._refuse_on_replica(tunnel_name):
            return False

        if tunnel_name != self.tunnel_name:
            self._select(tunnel_name)
        if not self.create_tunnel(tunnel_name, setup_auto_start=False):
            return False

        ok, dns_cert = self._connection_for(domain, tunnel_name)
        if not ok:
            return False
        tunnel_id = self._tunnel_id(tunnel_name)
        if not tunnel_id:
            self.out("Could not find tunnel ID")
            return False

        try:
            config = self._load_config(strict=True)
        except ConfigError as e:
            self.out(str(e))
            return False
        first_route = not self._routes_from_config(config)
        # Only offered on the CLI: the web UI asks about auto-start when the
        # tunnel is created and must not silently override that choice.
        if (self.interactive and first_route and self.auto_start_on_create
                and not self.check_autostart_status(verbose=False)):
            self.out("\nTip: You're adding your first route.")
            if self._ask("Would you like the tunnel to start automatically? (y/n): "):
                self.setup_autostart()

        config["tunnel"] = tunnel_id
        if os.path.exists(paths.credentials_file(tunnel_id)):
            config["credentials-file"] = paths.credentials_file(tunnel_id)

        service = f"http://{host}:{port}"
        ingress = [r for r in (config.get("ingress") or [])
                   if not (isinstance(r, dict) and _same_host(r.get("hostname"), domain))]
        # New rules go before the catch-all (whatever its service), which must stay last
        catch_all = next((i for i, r in enumerate(ingress) if _is_catch_all(r)), None)
        if catch_all is None:
            ingress += [{"hostname": domain, "service": service}, {"service": "http_status:404"}]
        else:
            ingress.insert(catch_all, {"hostname": domain, "service": service})
        config["ingress"] = ingress
        self._save_config(config)

        with self.state.locked():
            routes = [r for r in self.state.get(tunnel_name).get("routes", []) if not _same_host(r.get("domain"), domain)]
            routes.append({"domain": domain, "service": service})
            self.state.update(tunnel_name, {"routes": routes, "tunnel_id": tunnel_id})

        self._route_dns(domain, tunnel_name, dns_cert)
        self._restart_tunnel()
        self.out(f"Route added: {domain} -> {service}")
        return True

    def edit_route(self, tunnel_name: str, domain: str, new_domain: Optional[str] = None,
                   end_user_url: Optional[str] = None, localhost_port=None) -> bool:
        """Change a route's hostname, service host and/or port (keeps its position)."""
        if self._refuse_on_replica(tunnel_name):
            return False
        domain = strip_scheme(domain).lower()
        try:
            config = self._load_config(tunnel_name, strict=True)
        except ConfigError as e:
            self.out(str(e))
            return False
        ingress = config.get("ingress") or []
        index = next((i for i, r in enumerate(ingress) if isinstance(r, dict) and _same_host(r.get("hostname"), domain)), None)
        if index is None:
            self.out(f"Route for {domain} not found")
            return False
        old_hostname = ingress[index].get("hostname")
        old_service = str(ingress[index].get("service", ""))

        if end_user_url in (None, "") and localhost_port in (None, ""):
            # Only the hostname changes: keep the service exactly as it is (https://, paths, ssh://...)
            validated = self._validate_route(new_domain or domain, "localhost", 80)
            if not validated:
                return False
            new_domain, service = validated[0], old_service
        else:
            parts = urlsplit(old_service if "://" in old_service else "http://" + old_service)
            if parts.scheme not in ("http", "https"):
                self.out(f"{domain} points at {old_service}; only http(s) services can be changed here. "
                         "Edit the tunnel's configuration file for other kinds of service.")
                return False
            try:
                old_host, old_port = parts.hostname or "localhost", parts.port
            except ValueError:
                old_host, old_port = "localhost", None
            if ":" in old_host:
                old_host = f"[{old_host}]"  # IPv6
            validated = self._validate_route(
                new_domain or domain, end_user_url or old_host,
                localhost_port if localhost_port not in (None, "") else (old_port or (443 if parts.scheme == "https" else 80)))
            if not validated:
                return False
            new_domain, host, port = validated
            service = f"{parts.scheme}://{host}:{port}{parts.path or ''}"

        cert = None
        if not _same_host(new_domain, domain):
            if any(isinstance(r, dict) and _same_host(r.get("hostname"), new_domain) for r in ingress):
                self.out(f"{new_domain} is already routed by this tunnel")
                return False
            ok, cert = self._connection_for(new_domain, tunnel_name)
            if not ok:
                return False
        else:
            new_domain = old_hostname or new_domain  # keep the original spelling

        ingress[index] = {**ingress[index], "hostname": new_domain, "service": service}
        config["ingress"] = ingress
        self._save_config(config, tunnel_name)
        with self.state.locked():
            routes = [({"domain": new_domain, "service": service} if _same_host(r.get("domain"), domain) else r)
                      for r in self.state.get(tunnel_name).get("routes", [])]
            self.state.update(tunnel_name, {"routes": routes})

        if not _same_host(new_domain, domain):
            if self._route_dns(new_domain, tunnel_name, cert):
                self._cleanup_dns(domain, tunnel_name, self._tunnel_id(tunnel_name))
            else:
                self.out(f"Kept the DNS record of {domain} because the new one couldn't be created")

        previous = (self.tunnel_name, self.config_file)
        self._select(tunnel_name)
        try:
            self._restart_tunnel(start_if_stopped=False)
        finally:
            self.tunnel_name, self.config_file = previous
        self.out(f"Route updated: {new_domain} -> {service}")
        return True

    def remove_route(self, domain: str, tunnel_name: Optional[str] = None) -> bool:
        tunnel_name = tunnel_name or self.tunnel_name
        if not tunnel_name:
            self.out("No tunnel specified")
            return False
        domain = strip_scheme(domain).lower()
        if self._refuse_on_replica(tunnel_name):
            return False

        try:
            config = self._load_config(tunnel_name, strict=True)
        except ConfigError as e:
            self.out(str(e))
            return False
        if not isinstance(config.get("ingress"), list):
            self.out("No routes configured")
            return False
        before = len(config["ingress"])
        config["ingress"] = [r for r in config["ingress"]
                             if not (isinstance(r, dict) and _same_host(r.get("hostname"), domain))]
        if len(config["ingress"]) == before:
            self.out(f"Route for {domain} not found")
            return False
        self._save_config(config, tunnel_name)

        with self.state.locked():
            routes = [r for r in self.state.get(tunnel_name).get("routes", []) if not _same_host(r.get("domain"), domain)]
            self.state.update(tunnel_name, {"routes": routes})

        self._cleanup_dns(domain, tunnel_name, self._tunnel_id(tunnel_name))

        previous = (self.tunnel_name, self.config_file)
        self._select(tunnel_name)
        try:
            self._restart_tunnel(start_if_stopped=False)
        finally:
            self.tunnel_name, self.config_file = previous
        self.out(f"Route removed: {domain}")
        return True

    def list_routes(self, tunnel_name: Optional[str] = None) -> List[Dict]:
        tunnel_name = tunnel_name or self.tunnel_name
        if not tunnel_name:
            self.out("No tunnel specified")
            return []
        if tunnel_name in self.state.tunnels():
            return self.state.get(tunnel_name).get("routes", [])
        return self._routes_from_config(self._load_config(tunnel_name))

    def show_routes(self, tunnel_name: Optional[str] = None):
        tunnel_name = tunnel_name or self.tunnel_name
        if not tunnel_name:
            self.out("No tunnel specified")
            return
        routes = self.list_routes(tunnel_name)
        if not routes:
            self.out(f"No routes configured for tunnel '{tunnel_name}'")
            return
        self.out(f"\n=== Routes for tunnel '{tunnel_name}' ===")
        self.out(f"Status: {self.tunnel_status(tunnel_name)}")
        self.out("\n" + "=" * 70)
        self.out(f"{'Domain':<35} {'Service':<35}")
        self.out("=" * 70)
        for route in routes:
            self.out(f"{route['domain']:<35} {route['service']:<35}")
        self.out("=" * 70 + "\n")

    # ------------------------------------------------------------------ #
    # Running
    # ------------------------------------------------------------------ #

    def metrics_port(self, tunnel_name: str) -> int:
        """Fixed local port for the tunnel's health endpoint (cloudflared --metrics). If another
        account has taken the stored port (cloudflared would fail to start, and its answers
        would be trusted as this tunnel's health), a new one is chosen and the auto-start
        entry is rewritten with it."""
        port = self.state.get(tunnel_name).get("metrics_port")
        if port and cloudflared.port_is_ours(port):
            return port
        new = self._free_metrics_port(tunnel_name, exclude={port})
        if port and new and self.state.get(tunnel_name).get("auto_start"):
            backend = self._backend()
            if backend and backend.is_enabled(tunnel_name):
                self.out(f"Port {port} is taken by another program; '{tunnel_name}' now uses {new} for its health endpoint")
                backend.install(tunnel_name, paths.tunnel_config(tunnel_name), new)
        return new

    def _free_metrics_port(self, tunnel_name: str, exclude=()) -> int:
        import socket
        used = {i.get("metrics_port") for i in self.state.tunnels().values()} | set(exclude)
        for candidate in range(20300, 21000):
            if candidate in used:
                continue
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", candidate)) != 0:  # nothing listening
                    self.state.update(tunnel_name, {"metrics_port": candidate})
                    return candidate
        return 0

    def health(self, tunnel_name: str) -> Optional[Dict]:
        """This machine's connection state for the tunnel (from cloudflared itself)."""
        return cloudflared.local_health(self.state.get(tunnel_name).get("metrics_port"))

    def _spawn(self, tunnel_name: str) -> str:
        log = paths.tunnel_log(tunnel_name)
        processes.spawn_detached(cloudflared.run_command(tunnel_name, paths.tunnel_config(tunnel_name),
                                                         metrics_port=self.metrics_port(tunnel_name) or None), log)
        return log

    def _restart_tunnel(self, start_if_stopped: bool = True):
        """Apply a config change: restart via the auto-start service, or directly. With
        `start_if_stopped=False` a stopped tunnel stays stopped (editing or removing a route must
        not bring back online a tunnel its owner stopped); the change applies at its next start."""
        if not self.tunnel_name or not self.config_file or not os.path.exists(self.config_file):
            return
        if not start_if_stopped and not processes.tunnel_running(self.tunnel_name):
            self.out("The tunnel is stopped; the change applies when it starts")
            return
        backend = self._backend()
        if backend and backend.is_enabled(self.tunnel_name):
            self.out("Restarting service...")
            if backend.restart(self.tunnel_name):
                self.out("Service restarted")
                return
        self._stop_and_wait(self.tunnel_name)
        self._spawn(self.tunnel_name)
        self.out("Tunnel restarted")

    @staticmethod
    def _stop_and_wait(tunnel_name: str, timeout: float = 10.0):
        """Stop the tunnel's processes and wait until they are gone (the new process needs
        the same --metrics port), forcing them after `timeout` seconds."""
        processes.kill_tunnel(tunnel_name)
        deadline = time.time() + timeout
        while processes.tunnel_running(tunnel_name):
            if time.time() > deadline:
                processes.kill_tunnel(tunnel_name, force=True)
                time.sleep(0.5)
                break
            time.sleep(0.2)

    def start_tunnel(self, tunnel_name: Optional[str] = None) -> bool:
        tunnel_name = tunnel_name or self.tunnel_name
        if not self._known(tunnel_name):
            return False
        if not os.path.exists(paths.tunnel_config(tunnel_name)):
            self.out(f"No configuration found for tunnel '{tunnel_name}'. Add a route first.")
            return False
        if self.tunnel_status(tunnel_name).startswith("running"):
            self.out(f"Tunnel '{tunnel_name}' is already running")
            return True

        self.out(f"Starting tunnel '{tunnel_name}'...")
        backend = self._backend()
        if self.state.get(tunnel_name).get("auto_start") and backend and backend.start(tunnel_name):
            self.out(f"Tunnel started ({backend.name})")
            return True
        self.out(f"Tunnel started (logs: {self._spawn(tunnel_name)})")
        return True

    def resume_autostart_tunnels(self) -> List[str]:
        """Start every tunnel that has auto-start enabled.

        Run at container start: in Docker the crontab and start scripts live in
        the container layer and are lost when the container is recreated, but
        the state (in the ~/.cloudflared volume) remembers which tunnels should
        run. Missing auto-start entries are re-created as well.
        """
        started = []
        backend = self._backend()
        for name, info in self.state.tunnels().items():
            if info.get("temp_tunnel") or not info.get("auto_start"):
                continue
            if not os.path.exists(paths.tunnel_config(name)):
                continue
            self._select(name)
            if backend and not backend.is_enabled(name):
                self.out(f"Restoring auto-start for '{name}'")
                backend.install(name, paths.tunnel_config(name), self.metrics_port(name) or None)
            if self.start_tunnel(name):
                started.append(name)
        return started

    def stop_tunnel(self, tunnel_name: Optional[str] = None) -> bool:
        tunnel_name = tunnel_name or self.tunnel_name
        if not self._known(tunnel_name):
            return False
        self.out(f"Stopping tunnel '{tunnel_name}'...")
        backend = self._backend()
        if self.state.get(tunnel_name).get("auto_start") and backend:
            backend.stop(tunnel_name)
        processes.kill_tunnel(tunnel_name)  # also manually/cron-started copies
        self.out("Tunnel stopped")
        return True

    # ------------------------------------------------------------------ #
    # Auto-start
    # ------------------------------------------------------------------ #

    def setup_autostart(self) -> bool:
        if not self._known(self.tunnel_name):
            return False
        backend = self._backend()
        if not backend:
            self.out("Auto-start is not supported on this platform")
            return False
        if backend.install(self.tunnel_name, self.config_file, self.metrics_port(self.tunnel_name) or None):
            self.state.update(self.tunnel_name, {"auto_start": True})
            return True
        return False

    def remove_autostart(self, keep_running: bool = True) -> bool:
        """Stop starting the tunnel at boot. A running tunnel keeps running (removing a
        systemd/launchd/Task Scheduler entry stops its process, cron doesn't), unless
        `keep_running` is False (deleting the tunnel)."""
        if not self._known(self.tunnel_name):
            return False
        name = self.tunnel_name
        was_running = processes.tunnel_running(name)
        backend = self._backend()
        if backend and backend.remove(name):
            self.state.update(name, {"auto_start": False})
            if keep_running and was_running and not processes.tunnel_running(name) \
                    and os.path.exists(paths.tunnel_config(name)):
                self._spawn(name)
                self.out("The tunnel keeps running; it just won't start at boot any more")
            return True
        return False

    def check_autostart_status(self, verbose: bool = True) -> bool:
        if not self.tunnel_name:
            return False
        backend = self._backend()
        if not backend:
            return False
        if verbose:
            self.out(backend.describe(self.tunnel_name))
        return backend.is_enabled(self.tunnel_name)

    # ------------------------------------------------------------------ #
    # Delete
    # ------------------------------------------------------------------ #

    def delete_tunnel(self, tunnel_name: Optional[str] = None, force: bool = False) -> bool:
        tunnel_name = tunnel_name or self.tunnel_name
        if not tunnel_name:
            self.out("No tunnel specified")
            return False
        if not force and self.interactive:
            if input(f"Delete tunnel '{tunnel_name}' and all routes? (yes/no): ").strip().lower() != "yes":
                self.out("Cancelled")
                return False

        if not self._known(tunnel_name):
            return False
        previous = self.tunnel_name
        self._select(tunnel_name)
        replica = self.is_replica(tunnel_name)
        state_id = self.state.get(tunnel_name).get("tunnel_id")
        cert, remote_id = None, None
        if not replica:
            ok, cert = self._tunnel_cert_or_error(tunnel_name)
            remote, error = self._resolve_remote(tunnel_name) if ok else (None, None)
            if not ok or error:
                if error:
                    self.out(error)
                if previous and previous != tunnel_name:
                    self._select(previous)
                return False
            account = self.state.get(tunnel_name).get("account_id")
            if remote is None and (account in self._lookup_failed if account else self._lookup_failed):
                self.out(f"Cloudflare couldn't be asked whether '{tunnel_name}' still exists; nothing was "
                         "deleted. Try again.")
                if previous and previous != tunnel_name:
                    self._select(previous)
                return False
            # Read the ID before deleting: afterwards cloudflared no longer knows it
            remote_id = remote.get("id") if remote else None
        # The recorded ID still names the credentials file and DNS records when the
        # tunnel was already deleted in Cloudflare (e.g. from the dashboard).
        tunnel_id = remote_id or state_id
        target = remote_id or tunnel_name  # an ID is unambiguous across accounts
        hostnames = [r["domain"] for r in self.state.get(tunnel_name).get("routes", [])]

        if not replica and remote_id:
            # Refuse up front if other machines (replicas) still run it: deleting
            # would fail anyway, and we must not stop this machine's copy first.
            machines = cloudflared.connectors(target, cert) or []
            others = len(machines) - (1 if processes.tunnel_running(tunnel_name) else 0)
            if others > 0:
                self.out(f"Can't delete '{tunnel_name}': {others} other machine(s) are still running it (replicas). "
                         "Remove the replica on those machines first, then delete it here.")
                if previous and previous != tunnel_name:
                    self._select(previous)
                return False

        self.out(f"\nStopping tunnel '{tunnel_name}' and cleaning up connections...")
        had_autostart = self.check_autostart_status(verbose=False)
        was_running = self.tunnel_status(tunnel_name).startswith("running")
        if had_autostart:
            self.out("Removing auto-start configuration...")
            self.remove_autostart(keep_running=False)
        processes.kill_tunnel(tunnel_name)

        if replica:
            # Never delete the tunnel in Cloudflare from a replica: the other
            # machines (and the primary) keep serving it.
            self.out("Removed this machine's replica; the tunnel keeps running on its other machines.")
        elif remote_id is None:
            # Its account was listed and the tunnel isn't there any more (deleted in Cloudflare).
            # Never delete by name: another tunnel may carry that name now.
            self.out("Tunnel no longer exists in Cloudflare; removing it from Cloudify")
        else:
            time.sleep(3)
            cloudflared.run(["tunnel", "cleanup", target], cert=cert)
            self.out(f"\nDeleting tunnel '{tunnel_name}'...")
            for attempt in range(1, 4):
                result = cloudflared.run(["tunnel", "delete", target], cert=cert)
                if result.returncode == 0:
                    self.out("Tunnel deleted successfully")
                    break
                error = cloudflared.output(result)
                # Already deleted in Cloudflare (e.g. from the dashboard): just clean up here,
                # otherwise the tunnel could never be removed from Cloudify.
                gone = "neither the id nor the name" in error.lower() or "not found" in error.lower()
                if gone:  # deleted in Cloudflare in the meantime
                    self.out("Tunnel no longer exists in Cloudflare; removing it from Cloudify")
                    break
                if "active connection" in error.lower() and attempt < 3:
                    self.out(f"Active connections still exist. Retrying ({attempt}/3)...")
                    processes.kill_tunnel(tunnel_name, force=True)  # never every cloudflared
                    time.sleep(5)
                    cloudflared.run(["tunnel", "cleanup", target], cert=cert)
                    continue
                # Put this machine back the way it was before giving up
                if had_autostart:
                    self.setup_autostart()
                elif was_running:
                    self.start_tunnel(tunnel_name)
                if "active connection" in error.lower():
                    self.out(f"Failed to delete tunnel: other machines are still running it "
                             "(replicas). Remove the replica on those machines first, then delete it here.")
                else:
                    self.out(f"Failed to delete tunnel: {error}")
                if previous and previous != tunnel_name:
                    self._select(previous)
                return False

        for path in (paths.tunnel_config(tunnel_name),
                     paths.credentials_file(tunnel_id) if tunnel_id else None):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        if not replica and tunnel_id:
            for hostname in hostnames:  # never on a replica: the tunnel lives on elsewhere
                self._cleanup_dns(hostname, tunnel_name, tunnel_id)
        self.state.unregister(tunnel_name)

        self.out(f"\nTunnel '{tunnel_name}' and all associated configurations have been removed")
        if previous and previous != tunnel_name:
            self._select(previous)
        else:
            self.tunnel_name = self.config_file = None
        return True

    # ------------------------------------------------------------------ #
    # Temporary (quick) tunnels
    # ------------------------------------------------------------------ #

    def create_temp_tunnel(self, port, subdomain: Optional[str] = None, host: str = "localhost") -> Optional[str]:
        if subdomain:
            self.out("Note: quick tunnels always get a random *.trycloudflare.com address; "
                     "--subdomain is ignored. Use a named tunnel + route for your own domain.")
        if not is_valid_service_host(host) or parse_port(port) is None:
            self.out(f"Invalid address {host}:{port}")
            return None

        self.out(f"Creating temporary tunnel for {host}:{port}...")
        self.out("Waiting for tunnel URL...")
        try:
            process, url, error = quick_tunnel.start(host, int(port))
        except FileNotFoundError:
            self.out("cloudflared is not installed or not in PATH")
            return None
        if not url:
            self.out(f"Failed to create temporary tunnel: {error}")
            return None

        name = f"temp-{int(time.time())}-{process.pid}"  # unique even within the same second
        # Track it, but don't make it the "current" tunnel used by `route add`
        self.state.register(name, make_current=False)
        self.state.update(name, {"temp_tunnel": True, "temp_url": url, "temp_port": int(port),
                                 "temp_process_id": process.pid, "auto_start": False})
        self._temp_processes[process.pid] = process
        self.out("Temporary tunnel created!")
        self.out(f"URL: {url}")
        self.out(f"Local: {host}:{port}")
        self.out(f"Process ID: {process.pid}")
        return url

    def temp_tunnels(self) -> List[Dict]:
        """Running temporary tunnels (dead ones are cleaned from state)."""
        running = []
        for name, info in self.state.tunnels().items():
            if info.get("temp_tunnel"):
                if processes.is_quick_tunnel(info.get("temp_process_id")):
                    running.append(info)
                else:  # ended (or its PID now belongs to another program)
                    self.state.unregister(name)
        return running

    def list_temp_tunnels(self) -> List[Dict]:
        tunnels = self.temp_tunnels()
        if not tunnels:
            self.out("No temporary tunnels running")
            return tunnels
        self.out("\n=== Temporary Tunnels ===")
        self.out(f"{'URL':<50} {'Port':<8} {'PID'}")
        self.out("-" * 70)
        for t in tunnels:
            self.out(f"{t.get('temp_url', 'unknown'):<50} {t.get('temp_port', '?')!s:<8} {t.get('temp_process_id')}")
        return tunnels

    def stop_temp_tunnel(self, url: Optional[str] = None) -> bool:
        stopped = False
        for name, info in self.state.tunnels().items():
            if not info.get("temp_tunnel") or (url and info.get("temp_url") != url):
                continue
            pid = info.get("temp_process_id")
            if pid and processes.is_quick_tunnel(pid):  # never a process that reused the PID
                processes.kill_pid(pid)
                process = self._temp_processes.pop(pid, None)
                if process:
                    _wait_or_kill(process)
                self.out(f"Stopped temporary tunnel: {info.get('temp_url', name)}")
                stopped = True
            self.state.unregister(name)
        if not stopped:
            self.out(f"Temporary tunnel with URL {url} not found" if url else "No temporary tunnels running")
        return stopped

    def stop_own_temp_tunnels(self):
        """Stop the quick tunnels this process started, whatever the state file says (its record may
        be gone, e.g. removed by another `cloudify temp stop`)."""
        for pid, process in list(self._temp_processes.items()):
            if process.poll() is None:
                process.terminate()
                _wait_or_kill(process)
            self._temp_processes.pop(pid, None)

    # ------------------------------------------------------------------ #
    # CLI conveniences
    # ------------------------------------------------------------------ #

    def status(self, tunnel_name: Optional[str] = None):
        tunnel_name = tunnel_name or self.tunnel_name
        if not tunnel_name:
            self.list_all_tunnels()
            return True
        if not self._known(tunnel_name):
            return False
        state_id = self.state.get(tunnel_name).get("tunnel_id")
        remote = None
        if not self.is_replica(tunnel_name):
            remote, _ = self._resolve_remote(tunnel_name)
        tunnel_id = (remote or {}).get("id") or state_id
        self.out(f"\n=== Cloudflare Tunnel Status: {tunnel_name} ===")
        if not tunnel_id:
            self.out(f"\nTunnel '{tunnel_name}' was not found in the connected Cloudflare accounts")
        elif not remote and not self.is_replica(tunnel_name):
            self.out(f"\nTunnel: {tunnel_name} (ID: {tunnel_id}); Cloudflare could not confirm it exists "
                     "(offline, deleted, or its account isn't connected)")
        else:
            self.out(f"\nTunnel: {tunnel_name} (ID: {tunnel_id})")
        self.out("\nConfigured Routes:")
        routes = self.list_routes(tunnel_name)
        for route in routes:
            self.out(f"  • {route['domain']} -> {route['service']}")
        if not routes:
            self.out("  No routes configured")

        previous = self.tunnel_name
        self.tunnel_name = tunnel_name
        has_autostart = self.check_autostart_status(verbose=False)
        self.tunnel_name = previous
        self.out("\nAuto-start Configuration:")
        if has_autostart:
            self.out("  Enabled - Tunnel starts automatically")
        else:
            self.out(f"  Not enabled\n    Enable with: cloudify tunnel autostart {tunnel_name} --enable")

        status = self.tunnel_status(tunnel_name)
        if status.startswith("running"):
            self.out(f"\n  Tunnel is {status}")
        else:
            self.out(f"\n  Tunnel is not running\n    Start with: cloudify tunnel start {tunnel_name}")
        self.out()

    def quick_setup(self, tunnel_name: Optional[str] = None, enable_autostart: bool = True):
        self.out("\n=== Cloudflare Tunnel Quick Setup ===\n")
        if not self.accounts.is_connected():
            self.out("Step 1: Login to Cloudflare")
            if not self.login():
                return
        else:
            self.out("Already logged in to Cloudflare")

        if not tunnel_name and self.interactive:
            tunnel_name = input("\nEnter tunnel name: ").strip()
        if not tunnel_name:
            self.out("Tunnel name required")
            return

        self.out(f"\nStep 2: Setting up tunnel '{tunnel_name}'")
        if not self.create_tunnel(tunnel_name, setup_auto_start=False):
            self.out("Failed to create tunnel")
            return
        self.out("\nStep 3: Current configuration")
        self.show_routes(tunnel_name)
        if enable_autostart:
            self.out("\nStep 4: Setting up auto-start")
            self.out("Tunnel will start automatically" if self.setup_autostart()
                     else "Auto-start setup failed (manual setup may be required)")
        self.out("\nSetup complete!\n\nNext steps:")
        self.out(f"1. Add routes: cloudify route add app.example.com 3000 -t {tunnel_name}")
        self.out(f"2. Check status: cloudify tunnel status {tunnel_name}")


# Name used by earlier versions (and scripts built on them)
UniversalTunnelManager = TunnelManager
