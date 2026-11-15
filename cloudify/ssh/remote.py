"""`cloudify -ssh remote`: connect to a shared machine with an access code + password.

The code is decrypted locally; the one-time private key and the pinned host
key are written to a private temporary folder (deleted afterwards), and the
system `ssh` (or `sftp`) client connects through `cloudflared access ssh`.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence

from cloudify.core import cloudflared
from cloudify.ssh.credentials import AccessCodeError, open_access_code


class RemoteError(Exception):
    pass


# The host's SSH server ignores the user name (the session key is what counts), so a name
# that isn't a plain login name is replaced instead of being handed to ssh.
_LOGIN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}$")


def _login_name(user) -> str:
    return user if isinstance(user, str) and _LOGIN.fullmatch(user) else "cloudify"


def _run_cleaning_up(args: List[str]) -> int:
    """Run ssh/sftp; closing the terminal (SIGHUP) or `kill` (SIGTERM) still unwinds the
    caller's `finally`, so the one-time private key is never left on disk."""
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    previous = {}
    for name in ("SIGHUP", "SIGTERM"):
        if hasattr(signal, name):
            try:
                previous[name] = signal.signal(getattr(signal, name), stop)
            except (ValueError, OSError):  # not in the main thread
                pass
    try:
        return subprocess.call(args)
    finally:
        for name, handler in previous.items():
            signal.signal(getattr(signal, name), handler)


def ssh_options(payload: Dict, workdir: str) -> List[str]:
    """Options for ssh/sftp: one-time key, pinned host key, Cloudflare proxy."""
    key_file = os.path.join(workdir, "id_ed25519")
    known_hosts = os.path.join(workdir, "known_hosts")
    with open(os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(payload["key"])
    host = payload["host"]
    entry = f"[{host}]:{payload['port']}" if payload.get("port") else host
    with open(os.open(known_hosts, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(f"{entry} {payload['host_key']}\n")

    options = [
        "-i", key_file,
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", f"GlobalKnownHostsFile={os.devnull}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "LogLevel=ERROR",
    ]
    if payload.get("port"):  # direct TCP (local testing)
        options += ["-o", f"Port={payload['port']}"]
    else:
        proxy = cloudflared.find_binary()
        if not proxy:
            raise RemoteError("cloudflared is not installed on this machine "
                              "(https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)")
        options += ["-o", f'ProxyCommand="{proxy}" access ssh --hostname %h']
    return options


def _wait_for_dns(hostname: str, timeout: float = 30):
    """Brief retry for a tunnel address that is only seconds old."""
    import socket
    import time
    end = time.time() + timeout
    while True:
        try:
            socket.getaddrinfo(hostname, 443)
            return
        except socket.gaierror:
            if time.time() > end:
                raise RemoteError(f"Can't find {hostname} in DNS. Is the host still sharing? "
                                  "(the address changes every time sharing starts)")
            time.sleep(3)


def connect(code: str, password: str, command: Sequence[str] = (), sftp: bool = False,
            extra_args: Sequence[str] = ()) -> int:
    try:
        payload = open_access_code(code, password)
    except AccessCodeError as e:
        raise RemoteError(str(e))

    client = shutil.which("sftp" if sftp else "ssh")
    if not client:
        raise RemoteError(f"The OpenSSH `{'sftp' if sftp else 'ssh'}` client is not installed")

    workdir = tempfile.mkdtemp(prefix="cloudify-ssh-")
    try:
        os.chmod(workdir, 0o700)
        args = [client] + ssh_options(payload, workdir) + list(extra_args)
        target = f"{_login_name(payload.get('user'))}@{payload['host']}"
        if sftp:
            args += ["--", target]
        else:
            if not command:
                args.append("-t")  # interactive shell: ask for a terminal
            args += ["--", target] + list(command)
        if not payload.get("port"):
            _wait_for_dns(payload["host"])
        code = _run_cleaning_up(args)
        if code == 255 and not sftp:  # ssh's own connection-level failure
            sys.stderr.write("\ncloudify: the connection closed. The host may have stopped sharing; "
                             "ask for a new access code if this keeps happening.\n")
        return code
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
