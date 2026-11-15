"""`cloudflared tunnel login` with progress tracking for the web UI."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from cloudify.core import cloudflared, jsonfile, paths
from cloudify.core.accounts import AccountStore

LOGIN_URL_RE = re.compile(r'https://\S*cloudflare\S*')


LOGIN_TIMEOUT = 15 * 60  # seconds a Cloudflare login may wait for the user
TIMED_OUT = 124

def status_file() -> str:
    return os.path.join(paths.data_dir(), "status.json")


def log_file() -> str:
    return os.path.join(paths.data_dir(), "login.log")


def is_logged_in() -> bool:
    """At least one Cloudflare domain/account is connected."""
    return AccountStore().is_connected()


def update_status(status: str, url: Optional[str] = None, message: Optional[str] = None,
                  extra: Optional[Dict] = None):
    data = {
        "status": status,
        "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "url": url,
    }
    if message:
        data["message"] = message
    if extra:
        data.update(extra)
    try:
        jsonfile.write(status_file(), data)  # atomic and private (0600)
    except OSError as e:
        print(f"[!] Failed to update status: {e}")


# Connection fields the login status may carry: never certificate paths or API tokens
STATUS_CONNECTION_FIELDS = ("id", "domain", "account_id", "zone_id", "added_at")


def public_connection(record: Dict) -> Dict:
    return {k: record[k] for k in STATUS_CONNECTION_FIELDS if k in record}


def get_status() -> Optional[dict]:
    try:
        with open(status_file()) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    connection = data.get("connection")
    if isinstance(connection, dict) and set(connection) - set(STATUS_CONNECTION_FIELDS):
        # written by an older version, which copied the DNS API token here: scrub the file too
        data["connection"] = public_connection(connection)
        try:
            jsonfile.write(status_file(), data)
        except OSError:
            pass
    return data


def run_login(domain: Optional[str] = None, echo: bool = True) -> int:
    """Connect a Cloudflare domain. Blocks until the browser step finishes.

    cloudflared always writes ~/.cloudflared/cert.pem and refuses if one exists,
    so it runs with a temporary HOME; the new certificate is then stored as an
    extra connection. Status: starting -> url_found -> completed | error...
    """
    store = AccountStore()
    if not domain and store.is_connected():
        update_status("completed", message="Already connected to Cloudflare")
        if echo:
            print("Already connected to Cloudflare (use `cloudify account connect <domain>` to add another)")
        return 0

    # Leftovers of logins that were killed (they can hold a fresh certificate): remove them
    for old in glob.glob(os.path.join(paths.data_dir(), "cloudify-login-*")):
        try:
            if time.time() - os.path.getmtime(old) > 2 * LOGIN_TIMEOUT:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    temp_home = tempfile.mkdtemp(prefix="cloudify-login-", dir=paths.data_dir())
    env = dict(os.environ, HOME=temp_home, USERPROFILE=temp_home)
    if domain:
        env["CLOUDIFY_LOGIN_DOMAIN"] = domain
    try:
        code = run_command_live([cloudflared.binary(), "tunnel", "login"], echo=echo, env=env, finish=False,
                                timeout=LOGIN_TIMEOUT)
        cert = os.path.join(temp_home, ".cloudflared", "cert.pem")
        if code == TIMED_OUT:
            update_status("error", (get_status() or {}).get("url"),
                          message=f"The Cloudflare login wasn't completed within {LOGIN_TIMEOUT // 60} minutes. Start it again.")
            return code
        if code != 0 or not os.path.exists(cert):
            if code == 0:
                code = 1
            update_status(f"error_exit_{code}", message="Cloudflare login failed. See ~/.cloudflared/cloudify/login.log")
            return code
        record = store.add(cert, domain)
        # Only what the UI shows: never the certificate path or the stored DNS API token
        public = public_connection(record)
        update_status("completed", url=(get_status() or {}).get("url"), message=f"Connected {record['domain'] or 'Cloudflare account'}",
                      extra={"connection": public})
        if echo:
            print(f"Connected {record['domain'] or 'domain'} (account {record['account_id']})")
        return 0
    finally:
        shutil.rmtree(temp_home, ignore_errors=True)


def run_command_live(cmd: List[str], echo: bool = True, env: Optional[Dict] = None, finish: bool = True,
                     timeout: Optional[float] = None) -> int:
    """Run a command, mirror its output, and track progress in status.json.
    With finish=False the caller reports the final status. After `timeout` seconds the
    command is stopped and TIMED_OUT returned (a login nobody completes must not hold the
    dashboard's login slot forever)."""
    update_status("starting")
    url = None
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   bufsize=1, universal_newlines=True, env=env)
    except FileNotFoundError:
        update_status("error", message=f"{cmd[0]} is not installed")
        return 127

    expired = threading.Event()

    def expire():
        if process.poll() is None:
            expired.set()
            process.terminate()

    timer = threading.Timer(timeout, expire) if timeout else None
    if timer:
        timer.daemon = True
        timer.start()
    try:
        with os.fdopen(os.open(log_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as log:
            for line in iter(process.stdout.readline, ""):
                if echo:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                log.write(line)
                log.flush()
                found = LOGIN_URL_RE.search(line)
                if found and url is None:
                    url = found.group(0)
                    update_status("url_found", url)
                if finish and "successfully logged" in line.lower():
                    update_status("completed", url)
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        update_status("manually_stopped", url)
        raise
    finally:
        if timer:
            timer.cancel()
    process.wait()
    if expired.is_set():
        if finish:
            update_status("error", url, message="Stopped: it didn't finish in time")
        return TIMED_OUT
    if not finish:
        return process.returncode

    if process.returncode != 0:
        update_status(f"error_exit_{process.returncode}", url,
                      message="Cloudflare login failed. See ~/.cloudflared/cloudify/login.log")
    else:
        update_status("completed", url)
    return process.returncode
