"""Delete a tunnel's DNS records through the Cloudflare API.

cloudflared can create DNS records (`tunnel route dns`) but has no command to
delete them. Cloudify deletes them with the Cloudflare API instead, using an
API token with the "Zone · DNS · Edit" permission that the user adds per
domain (the login certificate's own token is tried first, in case it is
allowed to).

Safety: only a CNAME whose target is exactly `<tunnel id>.cfargotunnel.com`
is deleted, so records you pointed somewhere else are never touched.
"""

import os
from typing import Dict, List, Optional

API_BASE = "https://api.cloudflare.com/client/v4"


class DnsApiError(Exception):
    def __init__(self, message: str, permission: bool = False):
        super().__init__(message)
        self.permission = permission  # token not allowed (vs. network/other errors)


def _base() -> str:
    """API base URL. CLOUDIFY_CF_API (for tests) may only point at https, or at plain http on
    this machine: API tokens are sent with every request."""
    base = os.environ.get("CLOUDIFY_CF_API", API_BASE).rstrip("/")
    from urllib.parse import urlsplit
    parts = urlsplit(base)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")):
        return base
    raise DnsApiError(f"CLOUDIFY_CF_API must be an https:// URL (got {base})")


def _request(method: str, path: str, token: str, params: Optional[Dict] = None) -> Dict:
    import requests
    try:
        response = requests.request(method, _base() + path, params=params, timeout=15,
                                    headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as e:
        raise DnsApiError(f"Can't reach the Cloudflare API ({e.__class__.__name__})")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code in (401, 403) or (data.get("errors") and any(
            e.get("code") in (9109, 10000, 10001, 6003) for e in data.get("errors", []))):
        raise DnsApiError("The API token isn't allowed to edit DNS for this domain", permission=True)
    if not response.ok or data.get("success") is False:
        message = "; ".join(e.get("message", "") for e in data.get("errors", [])) or f"HTTP {response.status_code}"
        raise DnsApiError(f"Cloudflare API error: {message}")
    return data


def verify_token(token: str, zone_id: str):
    """Raise DnsApiError unless the token can read DNS records of the zone."""
    if not zone_id:
        raise DnsApiError("This domain's zone ID is unknown; reconnect it first")
    _request("GET", f"/zones/{zone_id}/dns_records", token, {"per_page": 1})


def tunnel_target(tunnel_id: str) -> str:
    return f"{tunnel_id}.cfargotunnel.com"


def record_status(tokens: List[str], zone_id: str, hostname: str, tunnel_id: str) -> str:
    """"ok" (CNAME -> this tunnel), "missing" or "other-target". Raises DnsApiError."""
    last_error = DnsApiError("No API token available")
    for token in [t for t in tokens if t]:
        try:
            records = _request("GET", f"/zones/{zone_id}/dns_records", token,
                               {"name": hostname, "per_page": 50}).get("result") or []
        except DnsApiError as e:
            last_error = e
            if not e.permission:
                break
            continue
        if not records:
            return "missing"
        if any((r.get("content") or "").lower() == tunnel_target(tunnel_id) for r in records):
            return "ok"
        return "other-target"
    raise last_error


def public_lookup(hostname: str) -> str:
    """Without an API token: "resolves" or "missing" (NXDOMAIN) via the system resolver."""
    import socket
    host = hostname[2:] if hostname.startswith("*.") else hostname
    if hostname.startswith("*."):
        host = "cloudify-dns-check." + host  # any name matches a wildcard
    try:
        socket.getaddrinfo(host, 443)
        return "resolves"
    except socket.gaierror as e:
        if e.errno in (getattr(socket, "EAI_NONAME", -2), getattr(socket, "EAI_NODATA", -5)):
            return "missing"
        return "unknown"


def delete_tunnel_record(tokens: List[str], zone_id: str, hostname: str, tunnel_id: str) -> str:
    """Delete the CNAME `hostname` -> this tunnel. Tries each token in turn.

    Returns "deleted", "missing" (no record) or "other-target" (record points
    elsewhere and was left alone). Raises DnsApiError if no token works.
    """
    last_error = DnsApiError("No API token available")
    for token in [t for t in tokens if t]:
        try:
            records = _request("GET", f"/zones/{zone_id}/dns_records", token,
                               {"type": "CNAME", "name": hostname, "per_page": 50}).get("result") or []
            if not records:
                return "missing"
            ours = [r for r in records if (r.get("content") or "").lower() == tunnel_target(tunnel_id)]
            if not ours:
                return "other-target"
            for record in ours:
                _request("DELETE", f"/zones/{zone_id}/dns_records/{record['id']}", token)
            return "deleted"
        except DnsApiError as e:
            last_error = e
            if not e.permission:
                break
    raise last_error
