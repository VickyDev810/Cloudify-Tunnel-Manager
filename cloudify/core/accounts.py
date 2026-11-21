"""Connected Cloudflare domains and accounts.

`cloudflared tunnel login` authorizes ONE domain (zone) and writes a cert.pem.
Creating DNS records only works with the cert of the hostname's own zone (with
another zone's cert cloudflared creates e.g. app.other.com.first.com), so
Cloudify keeps one certificate per connected domain and picks the right one:

* tunnel create/list/delete/cleanup -> any certificate of the tunnel's account
* route dns <hostname>              -> the certificate of the hostname's domain

Certificates live in ~/.cloudflared/cloudify/certs/<id>.pem and the list in
~/.cloudflared/cloudify/accounts.json. A pre-existing ~/.cloudflared/cert.pem
(from earlier versions or plain cloudflared) is registered as "default".
"""

import base64
import json
import os
import secrets
import shutil
from datetime import datetime
from typing import Dict, List, Optional

from cloudify.core import jsonfile, paths


def parse_cert(path: str) -> Dict[str, str]:
    """Read accountID/zoneID/apiToken from a cloudflared origin certificate."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return {}
    marker = "-----BEGIN ARGO TUNNEL TOKEN-----"
    if marker not in text:
        return {}
    body = text.split(marker, 1)[1].split("-----END", 1)[0]
    try:
        token = json.loads(base64.b64decode("".join(body.split())))
    except ValueError:
        return {}
    if not isinstance(token, dict):
        return {}
    return {
        "account_id": token.get("accountID", ""),
        "zone_id": token.get("zoneID", ""),
        "api_token": token.get("apiToken", ""),
    }


def lookup_zone_name(api_token: str, zone_id: str) -> Optional[str]:
    """Best effort: ask the Cloudflare API for the zone's name."""
    if not api_token or not zone_id or os.environ.get("CLOUDIFY_OFFLINE"):
        return None
    try:
        import requests
        response = requests.get(f"https://api.cloudflare.com/client/v4/zones/{zone_id}",
                                headers={"Authorization": f"Bearer {api_token}"}, timeout=5)
        if response.ok:
            return (response.json().get("result") or {}).get("name")
    except Exception:
        pass
    return None


def normalize_domain(domain: Optional[str]) -> str:
    return (domain or "").strip().lower().strip(".")


def domain_matches(hostname: str, domain: str) -> bool:
    hostname = hostname.lower()
    if hostname.startswith("*."):
        hostname = hostname[2:]
    return bool(domain) and (hostname == domain or hostname.endswith("." + domain))


def _copy_private(source: str, target: str):
    """Copy a certificate so that the copy is never readable by others, not even briefly."""
    with open(source, "rb") as f:
        data = f.read()
    tmp = f"{target}.{os.getpid()}.tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(data)
    os.replace(tmp, target)


class AccountStore:
    def __init__(self):
        self.path = os.path.join(paths.data_dir(), "accounts.json")
        self.cert_dir = os.path.join(paths.data_dir(), "certs")
        self._migrate_default_cert()

    # ------------------------------------------------------------- storage

    def _load(self) -> List[Dict]:
        data = jsonfile.read(self.path, {})
        connections = data.get("connections", []) if isinstance(data, dict) else []
        return [c for c in connections if isinstance(c, dict) and c.get("id") and c.get("cert")]

    def _save(self, connections: List[Dict]):
        jsonfile.write(self.path, {"connections": connections}, mode=0o600)  # may hold API tokens

    def _locked(self):
        return jsonfile.locked(self.path)

    def _migrate_default_cert(self):
        """Register ~/.cloudflared/cert.pem (the single cert of earlier versions)."""
        with self._locked():
            default = paths.cert_file()
            connections = self._load()
            if not os.path.exists(default) or any(c["cert"] == default for c in connections):
                return
            info = parse_cert(default)
            if info.get("zone_id") and any(c["zone_id"] == info["zone_id"] for c in connections):
                return  # just the copy of a connection we already have
            connections.insert(0, {
                "id": "default",
                "domain": normalize_domain(lookup_zone_name(info.get("api_token"), info.get("zone_id"))),
                "account_id": info.get("account_id", ""),
                "zone_id": info.get("zone_id", ""),
                "cert": default,
                "added_at": datetime.now().isoformat(),
            })
            self._save(connections)

    # --------------------------------------------------------------- queries

    def list(self) -> List[Dict]:
        """Connections that still have their certificate file."""
        return [c for c in self._load() if os.path.exists(c["cert"])]

    def public(self) -> List[Dict]:
        """Safe to show in the UI: no certificate paths or tokens."""
        return [{**{k: v for k, v in c.items() if k not in ("cert", "api_token")},
                 "dns_cleanup": bool(c.get("api_token"))} for c in self.list()]

    def dns_tokens(self, connection: Dict) -> List[str]:
        """Tokens for DNS API calls while automatic DNS cleanup is on: the user's API token, then the
        certificate's own. Without a stored token cleanup is off, as the dashboard says, so nothing
        is tried (not even the certificate's token)."""
        if not connection.get("api_token"):
            return []
        return [t for t in (connection.get("api_token"), parse_cert(connection["cert"]).get("api_token")) if t]

    def set_api_token(self, connection_id: str, token: Optional[str]) -> Dict:
        with self._locked():
            connections = self._load()
            for c in connections:
                if c["id"] == connection_id:
                    if token:
                        c["api_token"] = token
                    else:
                        c.pop("api_token", None)
                    self._save(connections)
                    return c
            raise KeyError(connection_id)

    def get(self, connection_id: str) -> Optional[Dict]:
        return next((c for c in self.list() if c["id"] == connection_id), None)

    def default_account_id(self) -> Optional[str]:
        """Account of ~/.cloudflared/cert.pem: the only account Cloudify 1.x knew."""
        if not os.path.exists(paths.cert_file()):
            return None
        return parse_cert(paths.cert_file()).get("account_id") or None

    def account_ids(self) -> List[str]:
        seen = []
        for c in self.list():
            if c["account_id"] not in seen:
                seen.append(c["account_id"])
        return seen

    def domains(self, account_id: Optional[str] = None) -> List[str]:
        return [c["domain"] for c in self.list()
                if c["domain"] and (account_id is None or c["account_id"] == account_id)]

    def cert_for_account(self, account_id: Optional[str]) -> Optional[str]:
        """Any certificate of the account (enough for tunnel create/list/delete)."""
        connections = self.list()
        if account_id:
            connections = [c for c in connections if c["account_id"] == account_id]
        return connections[0]["cert"] if connections else None

    def for_hostname(self, hostname: str, account_id: Optional[str] = None) -> Optional[Dict]:
        """The connection whose domain contains `hostname` (longest match)."""
        candidates = [c for c in self.list() if account_id is None or c["account_id"] == account_id]
        matches = [c for c in candidates if domain_matches(hostname, c["domain"])]
        if matches:
            return max(matches, key=lambda c: len(c["domain"]))
        # Earlier versions didn't record domains: with a single unnamed
        # connection for the account, keep the old single-domain behaviour.
        unnamed = [c for c in candidates if not c["domain"]]
        if len(candidates) == 1 and unnamed:
            return unnamed[0]
        return None

    def is_connected(self) -> bool:
        return bool(self.list())

    # ------------------------------------------------------------- changes

    def add(self, cert_source: str, domain: Optional[str] = None) -> Dict:
        """Store a freshly downloaded certificate as a new connection."""
        with self._locked():
            info = parse_cert(cert_source)
            domain = normalize_domain(domain) or normalize_domain(
                lookup_zone_name(info.get("api_token"), info.get("zone_id")))
            connections = self._load()
            # Logging in to the same zone again just refreshes its certificate.
            existing = next((c for c in connections if info.get("zone_id") and c["zone_id"] == info["zone_id"]), None)
            paths.private_dir(self.cert_dir)
            connection_id = existing["id"] if existing else secrets.token_hex(4)
            target = existing["cert"] if existing else os.path.join(self.cert_dir, f"{connection_id}.pem")
            _copy_private(cert_source, target)
            record = {
                "id": connection_id,
                "domain": domain or (existing or {}).get("domain", ""),
                "account_id": info.get("account_id", ""),
                "zone_id": info.get("zone_id", ""),
                "cert": target,
                "added_at": (existing or {}).get("added_at") or datetime.now().isoformat(),
            }
            if existing and existing.get("api_token"):
                record["api_token"] = existing["api_token"]
            if existing:
                connections[connections.index(existing)] = record
            else:
                connections.append(record)
            self._save(connections)
            # Plain `cloudflared` commands look for ~/.cloudflared/cert.pem; give them the first one.
            if not os.path.exists(paths.cert_file()):
                _copy_private(target, paths.cert_file())
            return record

    def set_domain(self, connection_id: str, domain: str) -> Dict:
        """Name the domain of a connection made by an older version (which didn't record it).
        A named connection can't be relabelled: the label decides which zone DNS changes go to."""
        from cloudify.core.validation import is_valid_domain
        domain = normalize_domain(domain)
        if not is_valid_domain(domain) or domain.startswith("*."):
            raise ValueError("Enter a domain like example.com")
        with self._locked():
            connections = self._load()
            for c in connections:
                if c["id"] == connection_id:
                    if c.get("domain") and c["domain"] != domain:
                        raise ValueError(f"This connection is already {c['domain']}; to use another domain, "
                                         "connect that domain instead")
                    c["domain"] = domain
                    self._save(connections)
                    return c
            raise KeyError(connection_id)

    def remove(self, connection_id: str) -> Dict:
        with self._locked():
            connections = self._load()
            record = next((c for c in connections if c["id"] == connection_id), None)
            if not record:
                raise KeyError(connection_id)
            connections.remove(record)
            self._save(connections)
            if record["cert"].startswith(self.cert_dir):
                try:
                    os.remove(record["cert"])
                except OSError:
                    pass
            # ~/.cloudflared/cert.pem is either this connection itself ("default") or a copy
            # of it; move it aside so it isn't registered again on the next start.
            default = paths.cert_file()
            if os.path.exists(default) and (record["cert"] == default or
                                            (record["zone_id"] and parse_cert(default).get("zone_id") == record["zone_id"])):
                try:
                    os.replace(default, default + ".removed")
                except OSError:
                    pass
            return record
