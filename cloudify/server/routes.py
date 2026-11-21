"""JSON API used by the web UI. Paths are unchanged from earlier versions."""

import logging
import os
import platform
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from cloudify import __version__
from cloudify.core import autostart, cloudflared, login as cf_login, replica
from cloudify.core.accounts import AccountStore
from cloudify.core.manager import AccountNotConnected, TunnelManager
from cloudify.server import auth
from cloudify.server.auth import current_user, optional_oauth2_scheme, oauth2_scheme

log = logging.getLogger("cloudify")
router = APIRouter()

_login_lock = threading.Lock()
_latest_cache = {"version": None, "checked": 0.0}


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #

class Credentials(BaseModel):
    username: str
    password: str
    setup_code: Optional[str] = None   # required for the first account (printed by `cloudify serve`)


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


class Username(BaseModel):
    username: str


class TunnelCreate(BaseModel):
    tunnel_name: str
    auto_start_on_create: Optional[bool] = True
    confirm_autostart: Optional[bool] = None
    account_id: Optional[str] = None


class ConnectDomain(BaseModel):
    domain: Optional[str] = None


class ConnectionId(BaseModel):
    id: str
    force: bool = False


class ConnectionDomain(BaseModel):
    id: str
    domain: str


class ConnectionToken(BaseModel):
    id: str
    token: str = ""


class RouteEdit(BaseModel):
    tunnel_name: str
    domain: str
    new_domain: Optional[str] = None
    end_user_url: Optional[str] = None
    localhost_port: Optional[int] = None


class ReplicaSelection(BaseModel):
    tunnels: List[str] = []
    hostnames: List[str] = []


class ReplicaSplit(BaseModel):
    tunnel_name: str
    hostnames: List[str]
    new_name: str


class ReplicaExport(BaseModel):
    tunnels: List[str]
    passphrase: str


class ReplicaImport(BaseModel):
    bundle: Dict
    passphrase: str
    autostart: bool = True


class TunnelDelete(BaseModel):
    tunnel_name: str
    force: Optional[bool] = False


class TunnelName(BaseModel):
    tunnel_name: str


class TunnelAdopt(BaseModel):
    tunnel_name: str
    account_id: Optional[str] = None


class RouteAdd(BaseModel):
    tunnel_name: str
    domain: str
    end_user_url: str = "localhost"
    localhost_port: int


class RouteRemove(BaseModel):
    tunnel_name: str
    domain: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def manager(tunnel_name: Optional[str] = None, **kwargs) -> TunnelManager:
    """A non-interactive manager whose messages go to the server log."""
    return TunnelManager(tunnel_name, interactive=False, out=lambda m: m and log.debug(m), **kwargs)


def managed(tunnel_name: str) -> TunnelManager:
    """Manager for a tunnel that must already be managed (404 otherwise; nothing is recorded)."""
    mgr = manager(tunnel_name)
    if tunnel_name not in mgr.state.tunnels():
        raise HTTPException(status_code=404, detail=f"Tunnel '{tunnel_name}' is not managed by Cloudify")
    return mgr


def ensure(ok: bool, mgr: TunnelManager, default: str):
    """Turn a failed manager call into a 400 with the manager's own reason."""
    if not ok:
        raise HTTPException(status_code=400, detail=mgr.last_error(default))


# --------------------------------------------------------------------------- #
# Setup & accounts
# --------------------------------------------------------------------------- #

@router.get("/setup/check")
def setup_check():
    """Public: tells the UI whether to show first-run setup or the login page."""
    count = auth.users.count()
    return {
        "needs_setup": count == 0,
        "has_users": count > 0,
        "user_count": count,
        "cloudflared_installed": cloudflared.find_binary() is not None,
        "cloudflare_logged_in": cf_login.is_logged_in(),
    }


@router.post("/auth/register")
def register(req: Credentials, token: Optional[str] = Depends(optional_oauth2_scheme)):
    """The first account can be created without logging in (initial setup), with the setup
    code that `cloudify serve` printed. After that only a logged-in user may add accounts,
    otherwise anyone who can reach the port could take over every tunnel."""
    username = req.username.strip()
    if not 1 <= len(username) <= 64:
        raise HTTPException(status_code=400, detail="Username must be 1-64 characters")
    auth.validate_password(req.password)
    with auth.users.lock:
        first = auth.users.count() == 0
        caller = auth.valid_user(token) if token else None
        if first and not auth.setup_code_matches(req.setup_code):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=(
                "Enter the setup code that `cloudify serve` printed when it started (with Docker: "
                "docker logs cloudify). Restart the dashboard to see it again."))
        if not first and not caller:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Registration is closed. Ask an existing user to create your account.")
        auth.users.create(username, req.password, is_admin=first)
    return {"message": "User registered successfully", "is_admin": first}


@router.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    user = auth.users.get(form.username)
    # an unknown name costs the same bcrypt check as a wrong password (timing doesn't reveal names)
    if not auth.verify_password(form.password, user["hashed_password"] if user else auth.dummy_hash()) or not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    # The token is bound to the account state that was just verified: if the password changes or the
    # account is deleted while this request runs, the token is useless.
    return {"access_token": auth.tokens.issue(user["username"], auth.session_epoch(user)), "token_type": "bearer"}


@router.post("/auth/logout")
def logout(token: str = Depends(oauth2_scheme)):
    auth.tokens.revoke(token)
    return {"message": "Logged out successfully"}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {"username": user["username"], "is_admin": user.get("is_admin", False)}


@router.post("/auth/change-password")
def change_password(req: ChangePassword, user: dict = Depends(current_user),
                    token: str = Depends(oauth2_scheme)):
    auth.validate_password(req.new_password)
    with auth.users.lock:
        stored = auth.users.get(user["username"])
        if not stored or not auth.verify_password(req.current_password, stored["hashed_password"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        epoch = auth.users.set_password(user["username"], req.new_password)
        auth.tokens.revoke_user(user["username"], keep=token)  # sign out other sessions...
        auth.tokens.rebind(token, epoch)                        # ...but not this one
    return {"message": "Password changed successfully"}


@router.get("/auth/users")
def list_users(user: dict = Depends(current_user)):
    return {"users": auth.users.public_list()}


@router.post("/auth/users/delete")
def delete_user(req: Username, user: dict = Depends(current_user), token: str = Depends(oauth2_scheme)):
    if req.username == user["username"]:
        raise HTTPException(status_code=400, detail="You can't delete your own account")
    with auth.users.lock:
        if not auth.valid_user(token):  # deleted by someone else meanwhile: two users can't delete each other
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
        if not auth.users.get(req.username):
            raise HTTPException(status_code=404, detail="User not found")
        auth.users.delete(req.username)
        auth.tokens.revoke_user(req.username)
    return {"message": f"User '{req.username}' deleted"}


# --------------------------------------------------------------------------- #
# Cloudflare login
# --------------------------------------------------------------------------- #

_login_domain = {"value": None}  # domain of the login in progress


def _run_login(domain: Optional[str] = None):
    try:
        cf_login.run_login(domain, echo=False)
    finally:
        _login_domain["value"] = None
        _login_lock.release()


def _start_login(background: BackgroundTasks, domain: Optional[str]):
    # Taken here, not in the background task, so two quick requests can't both start one
    if not _login_lock.acquire(blocking=False):
        busy = _login_domain["value"]
        raise HTTPException(status_code=409, detail=(
            f"Connecting {busy or 'a Cloudflare domain'} is still in progress. Finish it in the Cloudflare tab "
            "that opened (or wait a few minutes until it times out), then try again."))
    _login_domain["value"] = domain
    cf_login.update_status("starting")  # never let the UI read a stale result
    background.add_task(_run_login, domain)
    return {"message": "Login started"}


@router.post("/initial_setup")
def start_cloudflare_login(background: BackgroundTasks, user: dict = Depends(current_user)):
    """Connect Cloudflare (first account); poll GET /status for the URL/result."""
    return _start_login(background, None)


@router.get("/status")
def login_status(user: dict = Depends(current_user)):
    data = cf_login.get_status()
    if data is None:
        raise HTTPException(status_code=404, detail="Status not found")
    if isinstance(data.get("connection"), dict):  # defense in depth: no secrets in API responses
        data["connection"] = cf_login.public_connection(data["connection"])
    return data


# --------------------------------------------------------------------------- #
# Tunnels
# --------------------------------------------------------------------------- #

@router.get("/tunnels")
def list_tunnels(user: dict = Depends(current_user)):
    mgr = manager()
    return {"tunnels": mgr.list_all_tunnels(), "unmanaged": mgr.unmanaged_tunnels(), "user": user["username"]}


@router.post("/tunnel/create")
def create_tunnel(req: TunnelCreate, user: dict = Depends(current_user)):
    mgr = manager(req.tunnel_name, auto_start_on_create=bool(req.auto_start_on_create))
    ensure(mgr.create_tunnel(req.tunnel_name, setup_auto_start=req.confirm_autostart, account_id=req.account_id),
           mgr, "Failed to create tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' created"}


@router.post("/tunnel/delete")
def delete_tunnel(req: TunnelDelete, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.delete_tunnel(req.tunnel_name, force=True), mgr, "Failed to delete tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' deleted", "dns": mgr.dns_notes}


@router.post("/tunnel/adopt")
def adopt_tunnel(req: TunnelAdopt, user: dict = Depends(current_user)):
    mgr = manager()
    ensure(mgr.adopt_tunnel(req.tunnel_name, account_id=req.account_id or None), mgr, "Failed to adopt tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' adopted"}


@router.get("/tunnel/routes")
def tunnel_routes(tunnel_name: str = Query(...), user: dict = Depends(current_user)):
    return {"routes": managed(tunnel_name).list_routes(tunnel_name)}


@router.get("/tunnel/status")
def tunnel_status(tunnel_name: str = Query(...), user: dict = Depends(current_user)):
    mgr = managed(tunnel_name)
    return {"tunnel": tunnel_name, "status": mgr.tunnel_status(tunnel_name), "routes": mgr.list_routes(tunnel_name)}


@router.post("/tunnel/route/add")
def add_route(req: RouteAdd, user: dict = Depends(current_user)):
    mgr = manager(req.tunnel_name)
    ensure(mgr.add_route(req.domain, req.localhost_port, req.end_user_url, req.tunnel_name), mgr, "Failed to add route")
    return {"message": f"Route added: {req.domain} -> {req.end_user_url}:{req.localhost_port}"}


@router.post("/tunnel/route/edit")
def edit_route(req: RouteEdit, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.edit_route(req.tunnel_name, req.domain, req.new_domain, req.end_user_url, req.localhost_port),
           mgr, "Failed to update route")
    return {"message": mgr.last_error("Route updated"), "dns": mgr.dns_notes}


class DnsRepair(BaseModel):
    tunnel_name: str
    domain: str
    overwrite: bool = False


@router.get("/tunnel/dns")
def tunnel_dns(tunnel_name: str = Query(...), user: dict = Depends(current_user)):
    """DNS record status for each route (detects records deleted in the dashboard)."""
    return {"records": managed(tunnel_name).check_dns(tunnel_name)}


@router.post("/tunnel/route/dns")
def repair_route_dns(req: DnsRepair, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.repair_dns(req.tunnel_name, req.domain, req.overwrite), mgr, "Failed to create the DNS record")
    return {"message": mgr.last_error()}


@router.get("/tunnel/connectors")
def tunnel_connectors(tunnel_name: str = Query(...), user: dict = Depends(current_user)):
    """Machines currently running the tunnel."""
    mgr = managed(tunnel_name)
    target = mgr._tunnel_id(tunnel_name) or tunnel_name  # the ID is unambiguous across accounts
    try:
        cert = mgr._cert_for_tunnel(tunnel_name)
    except AccountNotConnected as e:
        raise HTTPException(status_code=409, detail=str(e))
    machines = cloudflared.connectors(target, cert)
    if machines is None:
        raise HTTPException(status_code=400, detail="Can't look up this tunnel's connections from this machine")
    return {"connectors": machines}


@router.post("/tunnel/route/remove")
def remove_route(req: RouteRemove, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.remove_route(req.domain, req.tunnel_name), mgr, "Failed to remove route")
    return {"message": f"Route removed: {req.domain}", "dns": mgr.dns_notes}


@router.post("/tunnel/start")
def start_tunnel(req: TunnelName, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.start_tunnel(req.tunnel_name), mgr, "Failed to start tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' started"}


@router.post("/tunnel/stop")
def stop_tunnel(req: TunnelName, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.stop_tunnel(req.tunnel_name), mgr, "Failed to stop tunnel")
    return {"message": f"Tunnel '{req.tunnel_name}' stopped"}


@router.post("/tunnel/autostart/setup")
def enable_autostart(req: TunnelName, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.setup_autostart(), mgr, "Failed to set up auto-start")
    return {"message": f"Auto-start enabled for '{req.tunnel_name}'"}


@router.post("/tunnel/autostart/remove")
def disable_autostart(req: TunnelName, user: dict = Depends(current_user)):
    mgr = managed(req.tunnel_name)
    ensure(mgr.remove_autostart(), mgr, "Failed to remove auto-start")
    return {"message": f"Auto-start removed for '{req.tunnel_name}'"}


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #

def _latest_cloudflared() -> Optional[str]:
    # Cached for an hour; a failed lookup (offline) is retried after 5 minutes, not on every call
    ttl = 3600 if _latest_cache["version"] else 300
    if time.time() - _latest_cache["checked"] > ttl:
        _latest_cache["checked"] = time.time()
        _latest_cache["version"] = cloudflared.latest_version(timeout=5) or _latest_cache["version"]
    return _latest_cache["version"]


@router.get("/system/info")
def system_info(user: dict = Depends(current_user)):
    installed = cloudflared.installed_version()
    latest = _latest_cloudflared()
    backend = autostart.get_backend(lambda m: None)
    return {
        "cloudify_version": __version__,
        "platform": f"{platform.system()} {platform.machine()}",
        "autostart_backend": backend.name if backend else None,
        "cloudflared": {
            "installed": installed is not None,
            "path": cloudflared.find_binary(),
            "version": installed,
            "latest": latest,
            "update_available": cloudflared.update_available(installed, latest),
        },
        "cloudflare_logged_in": cf_login.is_logged_in(),
        "config_dir": os.path.expanduser("~/.cloudflared"),
    }


@router.post("/system/cloudflared/update")
def update_cloudflared(user: dict = Depends(current_user)):
    ok, message = cloudflared.self_update()
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message}


# --------------------------------------------------------------------------- #
# Cloudflare accounts & domains
# --------------------------------------------------------------------------- #

@router.get("/accounts")
def list_accounts(user: dict = Depends(current_user)):
    store = AccountStore()
    connections = store.public()
    tunnels = manager().state.tunnels()
    by_account = {}
    for c in connections:
        entry = by_account.setdefault(c["account_id"], {"account_id": c["account_id"], "domains": [], "tunnels": []})
        entry["domains"].append(c["domain"])
    for name, info in tunnels.items():
        if info.get("account_id") in by_account and not info.get("temp_tunnel"):
            by_account[info["account_id"]]["tunnels"].append(name)
    routes = [r["domain"] for info in tunnels.values() for r in info.get("routes", [])]
    from cloudify.core.accounts import domain_matches
    for c in connections:
        c["routes"] = sum(1 for h in routes if domain_matches(h, c["domain"]))
    return {"connections": connections, "accounts": list(by_account.values()),
            "login_in_progress": _login_lock.locked()}


@router.post("/accounts/connect")
def connect_domain(req: ConnectDomain, background: BackgroundTasks, user: dict = Depends(current_user)):
    """Authorize another domain (possibly in another Cloudflare account)."""
    domain = (req.domain or "").strip().lower().strip(".")
    from cloudify.core.validation import is_valid_domain
    if not domain or not is_valid_domain(domain) or domain.startswith("*"):
        raise HTTPException(status_code=400, detail="Enter the domain you'll authorize, e.g. example.com")
    if domain in AccountStore().domains():
        raise HTTPException(status_code=400, detail=f"{domain} is already connected")
    return _start_login(background, domain)


@router.post("/accounts/domain")
def set_connection_domain(req: ConnectionDomain, user: dict = Depends(current_user)):
    """Name the domain of a connection made by an older version."""
    from cloudify.core.validation import is_valid_domain
    domain = req.domain.strip().lower().strip(".")
    if not is_valid_domain(domain):
        raise HTTPException(status_code=400, detail="Enter a domain like example.com")
    try:
        AccountStore().set_domain(req.id, domain)
    except KeyError:
        raise HTTPException(status_code=404, detail="Connection not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"Domain set to {domain}"}


@router.post("/accounts/token")
def set_connection_token(req: ConnectionToken, user: dict = Depends(current_user)):
    """API token (Zone · DNS · Edit) used to delete DNS records automatically."""
    from cloudify.core import dns_api
    store = AccountStore()
    connection = store.get(req.id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    token = req.token.strip()
    if token:
        try:
            dns_api.verify_token(token, connection["zone_id"])
        except dns_api.DnsApiError as e:
            raise HTTPException(status_code=400, detail=str(e))
    store.set_api_token(req.id, token or None)
    return {"message": "DNS cleanup enabled" if token else "API token removed"}


@router.post("/accounts/remove")
def remove_connection(req: ConnectionId, user: dict = Depends(current_user)):
    store = AccountStore()
    connection = store.get(req.id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    others = [c for c in store.list() if c["account_id"] == connection["account_id"] and c["id"] != req.id]
    tunnels = [n for n, i in manager().state.tunnels().items()
               if i.get("account_id") == connection["account_id"] and not i.get("replica") and not i.get("temp_tunnel")]
    if not others and tunnels and not req.force:
        raise HTTPException(status_code=409, detail=(
            f"This is the last domain of its Cloudflare account and {len(tunnels)} tunnel(s) use that account "
            f"({', '.join(tunnels)}). They keep running, but you won't be able to add routes to or delete them "
            "until the account is connected again."))
    store.remove(req.id)
    return {"message": f"Disconnected {connection['domain'] or 'domain'}"}


# --------------------------------------------------------------------------- #
# Replicas (redundancy across machines)
# --------------------------------------------------------------------------- #

def _replica_call(fn, *args):
    try:
        return fn(*args)
    except replica.ReplicaError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/replicas/plan")
def replica_plan(req: ReplicaSelection, user: dict = Depends(current_user)):
    return replica.plan(manager(), req.tunnels, req.hostnames)


@router.post("/replicas/split")
def replica_split(req: ReplicaSplit, user: dict = Depends(current_user)):
    mgr = manager()
    ensure(replica.split(mgr, req.tunnel_name, req.hostnames, req.new_name), mgr, "Failed to split tunnel")
    return {"message": mgr.last_error(), "tunnel": req.new_name}


@router.post("/replicas/export")
def replica_export(req: ReplicaExport, user: dict = Depends(current_user)):
    """Encrypted bundle; import it on another machine to run the tunnels there too."""
    return _replica_call(replica.export, manager(), req.tunnels, req.passphrase)


@router.post("/replicas/import")
def replica_import(req: ReplicaImport, user: dict = Depends(current_user)):
    mgr = manager()
    names = _replica_call(replica.import_bundle, mgr, req.bundle, req.passphrase, req.autostart)
    return {"message": mgr.last_error(), "tunnels": names}


@router.get("/replicas/status")
def replica_status(user: dict = Depends(current_user)):
    """Live view of tunnels that run on several machines.

    `machines` is Cloudflare's view (every machine connected for the tunnel);
    it needs a Cloudflare login for the tunnel's account, so a replica machine
    without one only reports its own health (`local`, from cloudflared itself).
    """
    from datetime import datetime, timezone
    mgr = manager()
    tunnels = []
    for name, info in mgr.state.tunnels().items():
        if info.get("temp_tunnel") or not (info.get("replica") or info.get("exported")):
            continue
        account_id = info.get("account_id")
        cert = mgr.accounts.cert_for_account(account_id) if account_id in mgr.accounts.account_ids() else None
        machines = cloudflared.connectors(name, cert) if cert else None
        local = mgr.health(name)
        if machines is not None:
            for machine in machines:
                machine["this_machine"] = bool(local and machine.get("id") == local.get("connector_id"))
        exported = info.get("exported")
        tunnels.append({
            "name": name,
            "role": "replica" if info.get("replica") else "primary",
            "status": mgr.tunnel_status(name),
            "routes": [r["domain"] for r in info.get("routes", [])],
            "replica": info.get("replica"),
            "exported": exported,
            "outdated": bool(exported and exported.get("config_hash") != mgr.config_hash(name)),
            "local": local,
            "machines": machines,
        })
    return {"tunnels": tunnels, "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
