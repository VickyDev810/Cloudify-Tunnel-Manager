"""Thin wrapper around the cloudflared binary."""

import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"
RELEASES_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"


def _executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _on_path() -> Optional[str]:
    """cloudflared from PATH, ignoring empty and relative entries (they mean the current folder, and the
    path found there would be run, and written into auto-start files, from whatever folder Cloudify was
    started in). Unlike shutil.which on Windows, the current folder is never searched first."""
    names = ["cloudflared.exe", "cloudflared"] if IS_WINDOWS else ["cloudflared"]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not os.path.isabs(folder):
            continue
        for name in names:
            path = os.path.join(folder, name)
            if _executable(path):
                return path
    return None


def find_binary() -> Optional[str]:
    """Absolute path of cloudflared, or None if it is not installed."""
    found = _on_path()
    if found:
        return found
    candidates = [
        "/usr/local/bin/cloudflared",
        "/usr/bin/cloudflared",
        "/opt/homebrew/bin/cloudflared",
        os.path.expanduser("~/.local/bin/cloudflared"),
    ]
    if IS_WINDOWS:
        candidates += [
            r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
            r"C:\Program Files\cloudflared\cloudflared.exe",
        ]
    return next((p for p in candidates if _executable(p)), None)


def binary() -> str:
    return find_binary() or "cloudflared"


def run(args: List[str], cert: Optional[str] = None, **kwargs) -> subprocess.CompletedProcess:
    """Run `cloudflared <args>`; returns exit code 127 instead of raising if missing.

    `cert` selects the origin certificate (Cloudflare account/domain) for
    `tunnel ...` commands.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    if cert and args[:1] == ["tunnel"]:
        args = ["tunnel", "--origincert", cert] + args[1:]
    try:
        return subprocess.run([binary()] + args, **kwargs)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, stdout="", stderr="cloudflared is not installed")


def output(result: subprocess.CompletedProcess) -> str:
    return ((result.stderr or "") + (result.stdout or "")).strip()


def list_tunnels_checked(cert: Optional[str] = None) -> Optional[List[Dict]]:
    """Tunnels in the certificate's Cloudflare account, or None if the list couldn't be read
    (offline, invalid certificate): "no tunnels" and "couldn't ask" must not be confused."""
    result = run(["tunnel", "list", "--output", "json"], cert=cert)
    if result.returncode != 0:
        return None
    try:
        tunnels = json.loads(result.stdout or "null")
    except (json.JSONDecodeError, TypeError):
        return None
    if tunnels is None:
        return []
    return [t for t in tunnels if isinstance(t, dict)] if isinstance(tunnels, list) else None


def list_tunnels(cert: Optional[str] = None) -> List[Dict]:
    """Tunnels in the certificate's Cloudflare account (empty list on error)."""
    return list_tunnels_checked(cert) or []


def tunnel_id(name: str, cert: Optional[str] = None) -> Optional[str]:
    for tunnel in list_tunnels(cert):
        if tunnel.get("name") == name:
            return tunnel.get("id")
    return None


def connectors(name: str, cert: Optional[str] = None) -> Optional[List[Dict]]:
    """Machines currently running the tunnel (Cloudflare spreads traffic across them).

    Returns None if it could not be determined (e.g. no certificate here).
    """
    result = run(["tunnel", "info", "--output", "json", name], cert=cert)
    try:
        info = json.loads(result.stdout or "null") or {}
    except (json.JSONDecodeError, TypeError):
        return None
    if result.returncode != 0:
        return None
    machines = []
    for connector in info.get("conns") or []:
        edges = connector.get("conns") or []
        live = [e for e in edges if not e.get("is_pending_reconnect")]
        machines.append({
            "id": connector.get("id"),
            "origin_ip": connector.get("origin_ip") or next((e.get("origin_ip") for e in edges if e.get("origin_ip")), None),
            "version": connector.get("version"),
            "arch": connector.get("arch"),
            "run_at": connector.get("run_at"),
            "edge_connections": len(live),
            "edge_locations": sorted({e.get("colo_name") for e in live if e.get("colo_name")}),
        })
    return machines


def run_args(tunnel_name: str, config_file: str, metrics_port: Optional[int] = None) -> List[str]:
    """Arguments after the binary for running a named tunnel (also what processes match on)."""
    metrics = ["--metrics", f"127.0.0.1:{metrics_port}"] if metrics_port else []
    return ["tunnel"] + metrics + ["--config", config_file, "run", tunnel_name]


def run_command(tunnel_name: str, config_file: str, path: Optional[str] = None,
                metrics_port: Optional[int] = None) -> List[str]:
    return [path or binary()] + run_args(tunnel_name, config_file, metrics_port)


def listener_uids(port: int) -> Optional[set]:
    """uids of the processes listening on `port` at any local address, IPv4 and IPv6 (Linux):
    a listener on 0.0.0.0 or [::] also answers 127.0.0.1 and blocks cloudflared's bind.
    None where that can't be told (no /proc)."""
    uids, readable = set(), False
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        readable = True
        for row in rows:
            fields = row.split()
            try:
                if len(fields) > 7 and fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                    uids.add(int(fields[7]))  # 0A = LISTEN
            except (IndexError, ValueError):
                continue
    return uids if readable else None


def listener_uid(port: int) -> Optional[int]:
    """A uid other than ours listening on the port, else ours or None (kept for callers that want one)."""
    uids = listener_uids(port) or set()
    others = [u for u in uids if not hasattr(os, "getuid") or u != os.getuid()]
    return others[0] if others else (next(iter(uids)) if uids else None)


def port_is_ours(port: Optional[int]) -> bool:
    """False when another account listens on the port at any address (it must not be trusted or reused)."""
    if not port or not hasattr(os, "getuid"):
        return True
    uids = listener_uids(port)
    return uids is None or all(uid == os.getuid() for uid in uids)


def local_health(metrics_port: Optional[int]) -> Optional[Dict]:
    """This machine's view of a running tunnel, from cloudflared's local /ready endpoint.

    {"ready": bool, "connections": n, "connector_id": "..."}; None if unreachable, or if the
    port belongs to another account (its answer would say nothing about this tunnel).
    """
    if not metrics_port or not port_is_ours(metrics_port):
        return None
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{metrics_port}/ready", timeout=1.5) as response:
            data = json.loads(response.read(65536) or b"{}")
            status = response.status
    except urllib.error.HTTPError as e:  # 503 while not connected
        try:
            data, status = json.loads(e.read(65536) or b"{}"), e.code
        except ValueError:
            data, status = {}, e.code
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not port_is_ours(metrics_port):  # the port may have changed hands
        return None
    return {"ready": status == 200, "connections": data.get("readyConnections", 0),
            "connector_id": data.get("connectorId")}


# --------------------------------------------------------------------------- #
# Versions / updates
# --------------------------------------------------------------------------- #

def installed_version() -> Optional[str]:
    path = find_binary()
    if not path:
        return None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.split()  # "cloudflared version 2026.11.0 (built ...)"
    return parts[2] if len(parts) >= 3 else None


def latest_version(timeout: float = 10) -> Optional[str]:
    try:
        import requests
        response = requests.get(RELEASES_API, timeout=timeout)
        response.raise_for_status()
        return response.json().get("tag_name")
    except Exception:
        return None


def version_tuple(version: Optional[str]) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in (version or "").split("."))
    except ValueError:
        return ()


def update_available(installed: Optional[str], latest: Optional[str]) -> bool:
    return bool(installed and latest and version_tuple(installed) < version_tuple(latest))


_update_lock = threading.Lock()


def self_update() -> Tuple[bool, str]:
    """Run `cloudflared update` (one at a time). Returns (success, message)."""
    path = find_binary()
    if not path:
        return False, "cloudflared is not installed"
    if not _update_lock.acquire(blocking=False):
        return False, "An update of cloudflared is already running"
    try:
        return _self_update(path)
    finally:
        _update_lock.release()


def _self_update(path: str) -> Tuple[bool, str]:
    before = installed_version()
    try:
        result = subprocess.run([path, "update"], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "cloudflared update did not finish within 5 minutes"
    except OSError as e:
        return False, f"Could not run {path}: {e}"
    text = output(result)
    # A package-managed cloudflared only logs that it was installed by a package manager
    # (and may exit 0): nothing was updated.
    if "package manager" in text.lower():
        hint = ("brew upgrade cloudflared" if sys.platform == "darwin"
                else "sudo apt install --only-upgrade cloudflared (or your package manager)")
        return False, f"{text}\nUpdate with: {hint}"
    # cloudflared exits with code 11 after a successful self-update
    if result.returncode in (0, 11):
        after = installed_version()
        if result.returncode == 0 and after == before:
            return True, f"cloudflared {after} is already the latest version"
        return True, f"cloudflared is now {after}"
    if "permission" in text.lower():
        return False, f"{text}\nPermission denied writing {path}; try: sudo {path} update"
    return False, text or f"cloudflared update failed (exit {result.returncode})"
