"""Finding, starting and stopping cloudflared processes (Linux, macOS, Windows)."""

import base64
import os
import re
import signal
import subprocess
import sys
from typing import List, Optional

from cloudify.core import paths

IS_WINDOWS = sys.platform == "win32"


def tunnel_pattern(tunnel_name: str, config_file: Optional[str] = None) -> str:
    """Regex matching exactly `cloudflared tunnel --config <this config> run <tunnel_name>`.

    Anchored at the end so "app" never matches a tunnel called "app2", and tied
    to this user's config file so another user (or another Cloudify instance)
    running a tunnel with the same name is left alone.
    """
    config_file = config_file or paths.tunnel_config(tunnel_name)
    return rf"cloudflared.*tunnel.*--config {re.escape(config_file)} run {re.escape(tunnel_name)}$"


def find_pids(pattern: str) -> List[int]:
    """PIDs whose full command line matches the regex."""
    if IS_WINDOWS:
        # WMIC is gone from recent Windows and its LIKE has no regex support,
        # so use PowerShell/CIM. -EncodedCommand avoids all quoting problems.
        ps_pattern = pattern.replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
            "Where-Object { $_.CommandLine -and "
            "((($_.CommandLine -replace '\\.exe','') -replace '\"','').Trim() "
            f"-match '{ps_pattern}') }} | ForEach-Object {{ $_.ProcessId }}"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True, text=True,
            )
            return [int(p) for p in result.stdout.split() if p.strip().isdigit()]
        except OSError:
            return []
    try:
        # Only this user's processes: another account's process can claim any command line
        result = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", pattern], capture_output=True, text=True)
    except OSError:
        return []
    own = os.getpid()
    return [int(p) for p in result.stdout.split() if p.isdigit() and int(p) != own]


def pid_alive(pid) -> bool:
    if not pid:
        return False
    if IS_WINDOWS:
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in result.stdout
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    # A zombie (exited, not yet reaped) still answers kill(0)
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


def command_line(pid) -> Optional[str]:
    """The process's command line, or None if it isn't running (or can't be read)."""
    if not pid_alive(pid):
        return None
    if IS_WINDOWS:
        script = f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").CommandLine"
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                  capture_output=True, text=True).stdout.strip() or None
        except OSError:
            return None
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip() or None
    except OSError:
        pass
    try:  # macOS and other systems without /proc
        return subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(int(pid))],  # -ww: full command line
                              capture_output=True, text=True).stdout.strip() or None
    except (OSError, ValueError):
        return None


def is_quick_tunnel(pid) -> bool:
    """Is PID still the `cloudflared tunnel --url ...` it was? A recorded PID can belong to
    an unrelated program after a reboot or a long time, and must never be killed then."""
    command = command_line(pid) if pid else None
    return bool(command) and "cloudflared" in command and " --url " in f" {command} "


def kill_pid(pid: int, force: bool = False):
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass


def tunnel_running(tunnel_name: str, config_file: Optional[str] = None) -> bool:
    return bool(find_pids(tunnel_pattern(tunnel_name, config_file)))


def kill_tunnel(tunnel_name: str, force: bool = False, config_file: Optional[str] = None):
    """Stop only the cloudflared processes running this tunnel."""
    for pid in find_pids(tunnel_pattern(tunnel_name, config_file)):
        kill_pid(pid, force=force)


def spawn_detached(cmd: List[str], log_path: str) -> subprocess.Popen:
    """Start a long-running process that outlives the CLI/server, logging to a file."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x8)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        )
    else:
        kwargs["start_new_session"] = True
    with open(log_path, "a") as log:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs)
