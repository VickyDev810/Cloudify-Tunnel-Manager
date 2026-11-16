"""Run the same tunnel on several machines for redundancy.

Cloudflare supports "replicas": when the same tunnel (same ID and credentials)
runs on several machines, Cloudflare spreads requests across all of them and
stops sending traffic to a machine that goes offline. No load-balancer setup
or DNS change is needed: the DNS records already point at the tunnel.

Every replica serves the tunnel's whole route list, so all machines must have
identical routes. To make only *some* hostnames redundant, `split()` first
moves them into their own tunnel (and re-points their DNS records), which can
then be replicated on its own.

Export produces a bundle with the tunnel credentials and routes, encrypted
with a passphrase (scrypt + AES-256-GCM). Importing it on another machine
starts the tunnel there as a replica; that machine needs no Cloudflare login.
"""

import base64
import hashlib
import json
import os
import re
import socket
from datetime import datetime
from typing import Dict, List, Optional

from cloudify import __version__
from cloudify.core import cloudflared, jsonfile, paths, processes
from cloudify.core.validation import NEW_TUNNEL_NAME_HELP, is_valid_domain, is_valid_tunnel_name

FORMAT = "cloudify-replica-bundle"
VERSION = 1
_AAD = b"cloudify-replica-bundle-v1"
_UUID = re.compile(r"[0-9a-fA-F-]{36}")
_SERVICE = re.compile(r"(https?://[A-Za-z0-9_.\[\]:-]+(:\d{1,5})?/?|http_status:\d{3})")  # used with fullmatch
# What an imported ingress rule may contain. Anything else (e.g. originRequest.bastionMode or
# proxyType, which turn the importer's cloudflared into a general-purpose proxy) is dropped.
_RULE_KEYS = {"hostname", "service", "path", "originRequest"}
_ORIGIN_REQUEST_KEYS = {"noTLSVerify", "originServerName", "httpHostHeader", "connectTimeout", "tlsTimeout",
                        "tcpKeepAlive", "keepAliveConnections", "keepAliveTimeout", "noHappyEyeballs",
                        "disableChunkedEncoding", "http2Origin", "access"}
_CREDENTIAL_KEYS = {"AccountTag", "TunnelSecret", "TunnelID", "Endpoint"}


def _safe_rule(rule: Dict) -> Dict:
    clean = {k: v for k, v in rule.items() if k in _RULE_KEYS}
    origin = clean.get("originRequest")
    if isinstance(origin, dict):
        origin = {k: v for k, v in origin.items() if k in _ORIGIN_REQUEST_KEYS}
        if origin:
            clean["originRequest"] = origin
        else:
            clean.pop("originRequest")
    else:
        clean.pop("originRequest", None)
    return clean


class ReplicaError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Encryption
# --------------------------------------------------------------------------- #

def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 15, r=8, p=1,
                          dklen=32, maxmem=64 * 1024 * 1024)


def _summary(tunnels: List[Dict]) -> List[Dict]:
    return [{"name": t["name"], "hostnames": [r["hostname"] for r in t["ingress"] if "hostname" in r]}
            for t in tunnels]


def encrypt(payload: Dict, passphrase: str) -> Dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(passphrase or "") < 8:
        raise ReplicaError("Use a passphrase of at least 8 characters")
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_key(passphrase, salt)).encrypt(nonce, json.dumps(payload).encode(), _AAD)
    b64 = lambda b: base64.b64encode(b).decode()
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at": payload["created_at"],
        "source": payload["source"],
        # readable summary so the file can be identified without the passphrase
        "tunnels": _summary(payload["tunnels"]),
        "kdf": {"name": "scrypt", "n": 2 ** 15, "r": 8, "p": 1, "salt": b64(salt)},
        "cipher": {"name": "AES-256-GCM", "nonce": b64(nonce)},
        "ciphertext": b64(ciphertext),
    }


def decrypt(bundle: Dict, passphrase: str) -> Dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ReplicaError("This is not a Cloudify replica file")
    if bundle.get("version") != VERSION:
        raise ReplicaError(f"Unsupported replica file version {bundle.get('version')}; update Cloudify")
    try:
        salt = base64.b64decode(bundle["kdf"]["salt"])
        nonce = base64.b64decode(bundle["cipher"]["nonce"])
        ciphertext = base64.b64decode(bundle["ciphertext"])
        plain = AESGCM(_key(passphrase or "", salt)).decrypt(nonce, ciphertext, _AAD)
    except InvalidTag:
        raise ReplicaError("Wrong passphrase, or the file was modified")
    except (KeyError, TypeError, ValueError):
        raise ReplicaError("The replica file is damaged")
    return json.loads(plain)


# --------------------------------------------------------------------------- #
# Planning / splitting
# --------------------------------------------------------------------------- #

def plan(manager, tunnels: List[str], hostnames: List[str]) -> Dict:
    """Work out what an export of these tunnels + hostnames needs.

    Returns {"whole": [tunnel names], "partial": {tunnel: {"selected": [...],
    "others": [...]}}, "unknown": [hostnames not routed by any tunnel]}.
    """
    owners = {}
    for name, info in manager.state.tunnels().items():
        for route in info.get("routes", []):
            owners[route["domain"]] = name
    whole = [t for t in tunnels if t in manager.state.tunnels()]
    partial: Dict[str, Dict] = {}
    unknown = []
    for hostname in hostnames:
        owner = owners.get(hostname.lower())
        if not owner:
            unknown.append(hostname)
        elif owner not in whole:
            partial.setdefault(owner, {"selected": [], "others": []})["selected"].append(hostname.lower())
    for owner, entry in list(partial.items()):
        all_routes = [r["domain"] for r in manager.state.get(owner).get("routes", [])]
        entry["others"] = [h for h in all_routes if h not in entry["selected"]]
        if not entry["others"]:  # every hostname selected -> just the whole tunnel
            whole.append(owner)
            del partial[owner]
    return {"whole": whole, "partial": partial, "unknown": unknown}


def split(manager, tunnel: str, hostnames: List[str], new_name: str) -> bool:
    """Move `hostnames` from `tunnel` into a new tunnel so they can be replicated alone."""
    out = manager.out
    if tunnel not in manager.state.tunnels():
        out(f"Tunnel '{tunnel}' is not managed by Cloudify")
        return False
    if manager.is_replica(tunnel):
        out(f"'{tunnel}' is a replica; split it on the primary machine")
        return False
    if not is_valid_tunnel_name(new_name) or new_name in manager.state.tunnels():
        out(f"Choose another name for the new tunnel ('{new_name}' is invalid or already used)")
        return False
    hostnames = sorted({h.strip().lower() for h in hostnames if h and h.strip()})
    if not hostnames:
        out("Choose at least one hostname to move")
        return False
    try:
        config = manager._load_config(tunnel, strict=True)
    except ValueError as e:
        out(str(e))
        return False
    ingress = config.get("ingress") or []
    moving = [r for r in ingress if isinstance(r, dict) and str(r.get("hostname", "")).lower() in hostnames]
    if len(moving) != len(hostnames):
        out(f"Not all of {', '.join(hostnames)} are routed by '{tunnel}'")
        return False

    # Check every hostname's domain is connected before changing anything
    certs = {}
    for rule in moving:
        ok, cert = manager._connection_for(rule["hostname"], tunnel)
        if not ok:
            return False
        certs[str(rule["hostname"]).lower()] = cert

    account_id = manager.account_of(tunnel)
    # Names are only unique within one Cloudflare account: a tunnel with the new name in any connected
    # account would be mistaken for the new one, so the name must be free everywhere.
    remote = manager.remote_tunnels()
    if manager._lookup_failed:
        out("Cloudflare couldn't be asked for the tunnels of every connected account; nothing was moved. Try again.")
        return False
    if any(t.get("name") == new_name for t in remote):
        out(f"A tunnel called '{new_name}' already exists in a connected Cloudflare account; choose another name")
        return False
    if not manager.create_tunnel(new_name, setup_auto_start=False, account_id=account_id):
        return False
    new_id = manager.state.get(new_name).get("tunnel_id")
    if not new_id:
        try:
            new_id = cloudflared.tunnel_id(new_name, manager._cert_for_tunnel(new_name))
        except ValueError as e:  # AccountNotConnected
            raise ReplicaError(str(e))
    if not new_id or not os.path.exists(paths.collect_credentials(new_id)):
        out(f"The new tunnel '{new_name}' wasn't created on this machine; nothing was moved")
        return False

    def rules(names):
        return [r for r in moving if str(r["hostname"]).lower() in names]

    new_config = {"tunnel": new_id, "credentials-file": paths.credentials_file(new_id),
                  "ingress": moving + [{"service": "http_status:404"}]}
    manager._save_config(new_config, new_name)
    manager.state.update(new_name, {"routes": [{"domain": r["hostname"], "service": r["service"]} for r in moving],
                                    "tunnel_id": new_id, "split_from": tunnel})

    # Start the new tunnel and point DNS at it while the old tunnel still serves the hostnames, so they
    # never go dark; a hostname leaves the old tunnel only once its DNS record points at the new one.
    previous = (manager.tunnel_name, manager.config_file)
    manager._select(new_name)
    if manager.state.get(tunnel).get("auto_start"):
        manager.setup_autostart()
    if not manager.tunnel_status(new_name).startswith("running"):
        manager.start_tunnel(new_name)
    moved = [h for h in hostnames if manager._route_dns(h, new_name, certs[h], overwrite=True)]
    failed = [h for h in hostnames if h not in moved]
    if failed:
        new_config["ingress"] = rules(moved) + [{"service": "http_status:404"}]
        manager._save_config(new_config, new_name)
        manager.state.update(new_name, {"routes": [{"domain": r["hostname"], "service": r["service"]}
                                                   for r in rules(moved)]})
        manager._restart_tunnel()
    if moved:
        config["ingress"] = [r for r in ingress if r not in rules(moved)]
        manager._save_config(config, tunnel)
        manager.state.update(tunnel, {"routes": [r for r in manager.state.get(tunnel).get("routes", [])
                                                 if r["domain"].lower() not in moved]})
        manager._select(tunnel)
        manager._restart_tunnel()
    manager.tunnel_name, manager.config_file = previous
    if failed:
        out(f"Couldn't point {', '.join(failed)} at '{new_name}'; "
            f"{'they stay' if len(failed) > 1 else 'it stays'} on '{tunnel}'")
        return False
    out(f"Moved {', '.join(hostnames)} to the new tunnel '{new_name}'")
    return True


# --------------------------------------------------------------------------- #
# Export / import
# --------------------------------------------------------------------------- #

def export(manager, tunnels: List[str], passphrase: str) -> Dict:
    """Encrypted bundle that makes another machine a replica of `tunnels`."""
    if not tunnels:
        raise ReplicaError("Choose at least one tunnel or hostname to replicate")
    items = []
    for name in tunnels:
        info = manager.state.get(name)
        if not info:
            raise ReplicaError(f"Unknown tunnel '{name}'")
        if not is_valid_tunnel_name(name):  # import would refuse it (see _check_tunnel)
            raise ReplicaError(f"'{name}' can't be replicated because of its name. Move its hostnames into a "
                               f"new tunnel first: cloudify replica split '{name}' HOSTNAME... --name NEW-NAME")
        try:
            tunnel_id = info.get("tunnel_id") or cloudflared.tunnel_id(name, manager._cert_for_tunnel(name))
        except ValueError as e:  # AccountNotConnected: never look the name up in another account
            raise ReplicaError(str(e))
        if not tunnel_id:
            raise ReplicaError(f"Can't find '{name}' in Cloudflare")
        try:
            with open(paths.collect_credentials(tunnel_id)) as f:
                credentials = json.load(f)
        except (OSError, ValueError):
            raise ReplicaError(f"The credentials of '{name}' are not on this machine "
                               f"({paths.credentials_file(tunnel_id)}); export it from the machine that created it")
        try:
            ingress = manager._load_config(name, strict=True).get("ingress") or []
        except ValueError as e:
            raise ReplicaError(str(e))
        if not any(isinstance(r, dict) and r.get("hostname") for r in ingress):
            raise ReplicaError(f"'{name}' has no routes yet")
        # Refuse here what the other machine would refuse on import (see _check_tunnel)
        unsupported = [f"{r.get('hostname', '(catch-all)')} -> {r.get('service')}" for r in ingress
                       if not (isinstance(r, dict) and isinstance(r.get("service"), str) and _SERVICE.fullmatch(r["service"]))]
        if unsupported:
            raise ReplicaError(f"'{name}' can't be replicated: only http(s) services can be shared with other "
                               f"machines, but it has {', '.join(unsupported)}")
        items.append({"name": name, "tunnel_id": tunnel_id, "account_id": manager.account_of(name),
                      "credentials": credentials, "ingress": ingress, "auto_start": bool(info.get("auto_start"))})

    payload = {"created_at": datetime.now().isoformat(timespec="seconds"),
               "source": socket.gethostname(), "cloudify_version": __version__, "tunnels": items}
    bundle = encrypt(payload, passphrase)
    for item in items:
        if not manager.is_replica(item["name"]):
            manager.state.update(item["name"], {"exported": {"at": payload["created_at"],
                                                             "config_hash": manager.config_hash(item["name"])}})
    return bundle


def _check_tunnel(item: Dict):
    name, tunnel_id = item.get("name"), item.get("tunnel_id")
    credentials, ingress = item.get("credentials"), item.get("ingress")
    # The name was chosen on another machine and becomes a NEW tunnel here (file names, auto-start
    # entries, cloudflared's command line), so it must follow the rule for new tunnels.
    if not is_valid_tunnel_name(name):
        raise ReplicaError("The replica file contains a tunnel name this machine can't use. " + NEW_TUNNEL_NAME_HELP)
    if not isinstance(tunnel_id, str) or not _UUID.fullmatch(tunnel_id):
        raise ReplicaError("The replica file contains an invalid tunnel")
    if not isinstance(credentials, dict) or credentials.get("TunnelID") != tunnel_id \
            or not isinstance(credentials.get("TunnelSecret"), str) or set(credentials) - _CREDENTIAL_KEYS:
        raise ReplicaError(f"Invalid credentials for '{name}'")
    if not isinstance(ingress, list) or not ingress:
        raise ReplicaError(f"No routes for '{name}'")
    for rule in ingress:
        if not isinstance(rule, dict) or not isinstance(rule.get("service"), str) or not _SERVICE.fullmatch(rule["service"]):
            raise ReplicaError(f"Invalid route in '{name}'")
        if "hostname" in rule and not is_valid_domain(str(rule["hostname"])):
            raise ReplicaError(f"Invalid hostname in '{name}'")
        if "path" in rule and not isinstance(rule["path"], str):
            raise ReplicaError(f"Invalid route in '{name}'")


def import_bundle(manager, bundle: Dict, passphrase: str, autostart: bool = True) -> List[str]:
    """Run the bundle's tunnels on this machine as replicas (or update them)."""
    payload = decrypt(bundle, passphrase)
    items = payload.get("tunnels") or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ReplicaError("The replica file is damaged")
    for item in items:
        _check_tunnel(item)
    # The readable summary (shown before importing) is not encrypted: refuse a file whose summary
    # doesn't describe what is actually inside.
    if _summary(items) != bundle.get("tunnels"):
        raise ReplicaError("The replica file was modified: its summary doesn't match its encrypted contents")
    for item in items:
        # Never overwrite the credentials of a tunnel this machine runs under another name
        owner = next((n for n, i in manager.state.tunnels().items() if i.get("tunnel_id") == item["tunnel_id"]), None)
        if (owner and owner != item["name"]) or (not owner and os.path.exists(paths.credentials_file(item["tunnel_id"]))):
            raise ReplicaError(f"This machine already has tunnel {item['tunnel_id']}"
                               + (f" (as '{owner}')" if owner else "") + "; not importing over it")
        existing = manager.state.get(item["name"])
        if existing and not existing.get("replica"):
            if existing.get("tunnel_id") == item["tunnel_id"] or os.path.exists(paths.credentials_file(item["tunnel_id"])):
                raise ReplicaError(f"This machine already runs '{item['name']}' as the primary")
            if existing.get("tunnel_id") or existing.get("routes") or existing.get("account_id"):
                raise ReplicaError(f"A different tunnel called '{item['name']}' already exists here")
            # otherwise it's only an empty placeholder entry

    imported = []
    for item in items:
        name, tunnel_id = item["name"], item["tunnel_id"]
        updating = bool(manager.state.get(name))
        creds_path = paths.credentials_file(tunnel_id)
        jsonfile.write(creds_path, item["credentials"], mode=0o600)  # private from the start

        ingress = [_safe_rule(r) for r in item["ingress"] if "hostname" in r] + [{"service": "http_status:404"}]
        manager._save_config({"tunnel": tunnel_id, "credentials-file": creds_path, "ingress": ingress}, name)
        manager.state.register(name, make_current=False)
        manager.state.update(name, {
            "tunnel_id": tunnel_id,
            "account_id": item.get("account_id"),
            "routes": [{"domain": r["hostname"], "service": r["service"]} for r in ingress if "hostname" in r],
            "replica": {"source": payload.get("source"), "imported_at": datetime.now().isoformat(timespec="seconds"),
                        "bundle_created_at": payload.get("created_at"), "config_hash": manager.config_hash(name)},
        })

        previous = (manager.tunnel_name, manager.config_file)
        manager._select(name)
        if autostart and not manager.check_autostart_status(verbose=False):
            manager.setup_autostart()
        if processes.tunnel_running(name) or updating:
            manager._restart_tunnel()
        elif not manager.tunnel_status(name).startswith("running"):
            manager.start_tunnel(name)
        manager.tunnel_name, manager.config_file = previous
        manager.out(f"{'Updated' if updating else 'Started'} replica of '{name}' "
                    f"({len(ingress) - 1} hostname(s)) from {payload.get('source')}")
        imported.append(name)
    return imported
