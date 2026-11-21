"""Start tunnels automatically at boot/login, without root.

One backend per platform, all with the same interface:

* Linux with a systemd user session -> ``SystemdUserBackend``
* Linux without systemd (Docker etc.) -> ``CronBackend`` (@reboot)
* macOS                              -> ``LaunchAgentBackend``
* Windows                            -> ``TaskSchedulerBackend``
"""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Callable, Optional
from xml.sax.saxutils import escape as xml_escape

from cloudify.core import cloudflared, paths, processes

Output = Callable[[str], None]


def _metrics(port: Optional[int]) -> str:
    """Command-line fragment giving the tunnel a fixed local health/metrics address."""
    return f" --metrics 127.0.0.1:{port}" if port else ""


# Tunnels adopted from Cloudflare or imported from a replica file may have names with spaces
# and other characters (see validation.is_safe_tunnel_name). Every value written into a unit
# file, script, crontab line, plist or batch file is quoted for that format, and file/unit
# names use a safe spelling. Names made only of [A-Za-z0-9_.-] keep their old spelling, so
# entries created by earlier versions are still recognized.
_PLAIN_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _file_safe(tunnel: str) -> str:
    """A spelling of the name that is safe in file names, crontab lines and unit names."""
    if _PLAIN_NAME.fullmatch(tunnel):
        return tunnel
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", tunnel).strip("_") or "tunnel"
    return f"{readable}-{hashlib.sha256(tunnel.encode('utf-8')).hexdigest()[:8]}"


def _systemd_quote(value: str) -> str:
    """One ExecStart argument: quoted, with systemd's specifier (%) and variable ($) expansion off."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def _bat_quote(value: str) -> str:
    """One argument in a .bat file: quoted, with variable expansion (%) off."""
    return '"' + value.replace("%", "%%") + '"'


def _quiet(*cmd) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True)
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


class AutostartBackend:
    name = "none"

    def __init__(self, out: Output = print):
        self.out = out

    def install(self, tunnel: str, config_file: str, metrics_port: Optional[int] = None) -> bool:
        raise NotImplementedError

    def remove(self, tunnel: str) -> bool:
        raise NotImplementedError

    def is_enabled(self, tunnel: str) -> bool:
        raise NotImplementedError

    def is_active(self, tunnel: str) -> bool:
        """Whether the managed service itself is running the tunnel."""
        return processes.tunnel_running(tunnel)

    def start(self, tunnel: str) -> bool:
        return False

    def stop(self, tunnel: str):
        pass

    def restart(self, tunnel: str) -> bool:
        return False

    def describe(self, tunnel: str, verbose: bool = True) -> str:
        return "Enabled" if self.is_enabled(tunnel) else "Not enabled"


# --------------------------------------------------------------------------- #
# Linux: systemd --user
# --------------------------------------------------------------------------- #

class SystemdUserBackend(AutostartBackend):
    name = "systemd"

    @staticmethod
    def available() -> bool:
        if not sys.platform.startswith("linux"):
            return False
        if _quiet("systemctl", "--version").returncode != 0:
            return False
        return _quiet("systemctl", "--user", "status").returncode == 0

    @staticmethod
    def unit(tunnel: str) -> str:
        return f"cloudflared-tunnel-{_file_safe(tunnel)}"

    def unit_file(self, tunnel: str) -> str:
        return os.path.expanduser(f"~/.config/systemd/user/{self.unit(tunnel)}.service")

    def install(self, tunnel: str, config_file: str, metrics_port: Optional[int] = None) -> bool:
        self.out(f"\n=== Setting up User Systemd Service for '{tunnel}' ===")
        path = cloudflared.find_binary()
        if not path:
            self.out("cloudflared binary not found")
            return False

        # Make sure no manually started copy keeps running alongside the service
        processes.kill_tunnel(tunnel)

        unit_file = self.unit_file(tunnel)
        os.makedirs(os.path.dirname(unit_file), exist_ok=True)
        with open(unit_file, "w") as f:
            f.write(f"""[Unit]
Description=Cloudflare Tunnel - {tunnel.replace("%", "%%")}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={_systemd_quote(path)} tunnel{_metrics(metrics_port)} --config {_systemd_quote(config_file)} run {_systemd_quote(tunnel)}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
""")
        os.chmod(unit_file, 0o644)

        unit = f"{self.unit(tunnel)}.service"
        for step, cmd in (("Reloading user systemd...", ["daemon-reload"]),
                          ("Enabling service...", ["enable", unit])):
            self.out(step)
            result = _quiet("systemctl", "--user", *cmd)
            if result.returncode != 0:
                self.out(f"Failed to install service: {result.stderr.strip()}")
                return False

        if os.path.exists(config_file):
            self.out("Starting service...")
            if _quiet("systemctl", "--user", "restart", unit).returncode != 0:
                self.out("Failed to start service")
                return False
        else:
            self.out("Service will start once the first route is added")

        user = os.environ.get("USER") or os.environ.get("LOGNAME")
        if user:
            if _quiet("loginctl", "enable-linger", user).returncode == 0:
                self.out("Enabled user service lingering (starts at boot)")
            else:
                self.out("Note: Service will start when you log in (enable-linger failed)")

        self.out("\nService installed successfully!")
        self.out(f"  Check status:  systemctl --user status {self.unit(tunnel)}")
        self.out(f"  View logs:     journalctl --user -u {self.unit(tunnel)} -f")
        return True

    def remove(self, tunnel: str) -> bool:
        self.out("Removing user systemd service...")
        _quiet("systemctl", "--user", "stop", self.unit(tunnel))
        _quiet("systemctl", "--user", "disable", self.unit(tunnel))
        try:
            if os.path.exists(self.unit_file(tunnel)):
                os.remove(self.unit_file(tunnel))
        except OSError as e:
            self.out(f"Failed to remove user systemd service: {e}")
            return False
        _quiet("systemctl", "--user", "daemon-reload")
        self.out("User systemd service removed")
        return True

    def is_enabled(self, tunnel: str) -> bool:
        return _quiet("systemctl", "--user", "is-enabled", self.unit(tunnel)).returncode == 0

    def is_active(self, tunnel: str) -> bool:
        return _quiet("systemctl", "--user", "is-active", self.unit(tunnel)).returncode == 0

    def start(self, tunnel: str) -> bool:
        return _quiet("systemctl", "--user", "start", self.unit(tunnel)).returncode == 0

    def stop(self, tunnel: str):
        _quiet("systemctl", "--user", "stop", self.unit(tunnel))

    def restart(self, tunnel: str) -> bool:
        return _quiet("systemctl", "--user", "restart", self.unit(tunnel)).returncode == 0

    def describe(self, tunnel: str, verbose: bool = True) -> str:
        return (f"User Systemd Service Status:\n"
                f"  Enabled: {'Yes' if self.is_enabled(tunnel) else 'No'}\n"
                f"  Active:  {'Yes' if self.is_active(tunnel) else 'No'}")


# --------------------------------------------------------------------------- #
# Linux without systemd: cron @reboot
# --------------------------------------------------------------------------- #

class CronBackend(AutostartBackend):
    name = "cron"

    @staticmethod
    def script_name(tunnel: str) -> str:
        return f"start-tunnel-{_file_safe(tunnel)}.sh"

    @classmethod
    def script_path(cls, tunnel: str) -> str:
        return os.path.expanduser(f"~/.local/bin/{cls.script_name(tunnel)}")

    @staticmethod
    def _crontab() -> Optional[str]:
        result = _quiet("crontab", "-l")
        return result.stdout if result.returncode == 0 else None

    @staticmethod
    def _write_crontab(content: str) -> bool:
        try:
            proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=content)
            return proc.returncode == 0
        except OSError:
            return False

    def install(self, tunnel: str, config_file: str, metrics_port: Optional[int] = None) -> bool:
        self.out(f"\n=== Setting up Cron Auto-start for '{tunnel}' ===")
        path = cloudflared.find_binary()
        if not path:
            self.out("cloudflared binary not found")
            return False
        if not shutil.which("crontab"):
            self.out("crontab not available on this system")
            return False

        script = self.script_path(tunnel)
        os.makedirs(os.path.dirname(script), exist_ok=True)
        q = shlex.quote
        with open(script, "w") as f:
            f.write(f"""#!/bin/bash
# Auto-start script for tunnel {_file_safe(tunnel)}

# Kill any existing instances of this tunnel only
pkill -f {q(processes.tunnel_pattern(tunnel))} 2>/dev/null || true
sleep 2

if [ ! -f {q(config_file)} ]; then
    echo "No config for tunnel "{q(tunnel)}" yet (add a route first)"
    exit 0
fi

nohup {q(path)} tunnel{_metrics(metrics_port)} --config {q(config_file)} run {q(tunnel)} \\
    >> {q(paths.tunnel_log(tunnel))} 2>&1 &

echo "Tunnel "{q(tunnel)}" started at $(date)"
""")
        os.chmod(script, 0o700)

        current = self._crontab() or ""
        line = f"@reboot {q(script)}"
        if line not in current:
            new = current.rstrip("\n") + ("\n" if current.strip() else "") + line + "\n"
            if not self._write_crontab(new):
                self.out("Failed to add cron job")
                return False
            self.out("Cron job added successfully!")
        else:
            self.out("Cron job already exists")
        self.out(f"Script created: {script}")
        subprocess.run([script], capture_output=True)
        return True

    def remove(self, tunnel: str) -> bool:
        current = self._crontab()
        if current is not None:
            lines = current.split("\n")
            kept = [l for l in lines if not self._is_entry(l, tunnel)]
            if len(kept) != len(lines):
                if not self._write_crontab("\n".join(kept)):
                    self.out("Failed to remove cron job")
                    return False
                self.out("Cron job removed")
        try:
            if os.path.exists(self.script_path(tunnel)):
                os.remove(self.script_path(tunnel))
        except OSError:
            pass
        return True

    @classmethod
    def _is_entry(cls, line: str, tunnel: str) -> bool:
        """The crontab line that starts this tunnel (exact script name, not a prefix of another)."""
        name = cls.script_name(tunnel)
        return bool(re.search(rf"(^|[\s/'\"]){re.escape(name)}($|[\s'\"])", line))

    def is_enabled(self, tunnel: str) -> bool:
        if not shutil.which("crontab"):
            return False
        return any(self._is_entry(l, tunnel) for l in (self._crontab() or "").splitlines())

    # cron only starts tunnels at boot; start/stop/restart are done directly
    # on the process by the manager (base-class defaults).

    def describe(self, tunnel: str, verbose: bool = True) -> str:
        if not shutil.which("crontab"):
            return "Cron Status: crontab not available"
        return f"Cron Status: {'Enabled' if self.is_enabled(tunnel) else 'Not enabled'}"


# --------------------------------------------------------------------------- #
# macOS: LaunchAgent
# --------------------------------------------------------------------------- #

class LaunchAgentBackend(AutostartBackend):
    name = "launchd"

    @staticmethod
    def label(tunnel: str) -> str:
        return f"com.user.cloudflare.tunnel.{_file_safe(tunnel)}"

    def plist(self, tunnel: str) -> str:
        return os.path.expanduser(f"~/Library/LaunchAgents/{self.label(tunnel)}.plist")

    def install(self, tunnel: str, config_file: str, metrics_port: Optional[int] = None) -> bool:
        self.out(f"\n=== Setting up LaunchAgent for '{tunnel}' ===")
        path = cloudflared.find_binary()
        if not path:
            self.out("cloudflared binary not found")
            return False

        processes.kill_tunnel(tunnel)
        logs = os.path.expanduser("~/Library/Logs")
        os.makedirs(logs, exist_ok=True)
        os.makedirs(os.path.dirname(self.plist(tunnel)), exist_ok=True)

        metrics_args = ["--metrics", f"127.0.0.1:{metrics_port}"] if metrics_port else []
        metrics_xml = "".join(f"\n        <string>{xml_escape(arg)}</string>" for arg in metrics_args)
        x = xml_escape
        _quiet("launchctl", "unload", self.plist(tunnel))  # pick up a changed definition
        with open(self.plist(tunnel), "w") as f:
            f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{x(self.label(tunnel))}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{x(path)}</string>
        <string>tunnel</string>{metrics_xml}
        <string>--config</string>
        <string>{x(config_file)}</string>
        <string>run</string>
        <string>{x(tunnel)}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{x(logs)}/cloudflared-tunnel-{x(_file_safe(tunnel))}.log</string>
    <key>StandardErrorPath</key>
    <string>{x(logs)}/cloudflared-tunnel-{x(_file_safe(tunnel))}.error.log</string>
</dict>
</plist>""")

        self.out("Loading LaunchAgent...")
        result = _quiet("launchctl", "load", "-w", self.plist(tunnel))
        if result.returncode != 0:
            self.out(f"Failed to install LaunchAgent: {result.stderr.strip()}")
            return False
        self.out("\nLaunchAgent installed successfully!")
        self.out(f"  Check status:  launchctl list | grep {tunnel}")
        return True

    def remove(self, tunnel: str) -> bool:
        self.out("Removing LaunchAgent...")
        _quiet("launchctl", "unload", "-w", self.plist(tunnel))
        try:
            if os.path.exists(self.plist(tunnel)):
                os.remove(self.plist(tunnel))
        except OSError:
            self.out("Failed to remove LaunchAgent")
            return False
        self.out("LaunchAgent removed")
        return True

    def is_enabled(self, tunnel: str) -> bool:
        # `launchctl list` prints "PID<TAB>Status<TAB>Label"; compare the whole label so that
        # "web" isn't found inside "web-ha"
        label = self.label(tunnel)
        return any(line.split("\t")[-1].strip() == label
                   for line in _quiet("launchctl", "list").stdout.splitlines())

    def start(self, tunnel: str) -> bool:
        return _quiet("launchctl", "start", self.label(tunnel)).returncode == 0

    def stop(self, tunnel: str):
        _quiet("launchctl", "stop", self.label(tunnel))

    def restart(self, tunnel: str) -> bool:
        target = f"gui/{os.getuid()}/{self.label(tunnel)}"
        return _quiet("launchctl", "kickstart", "-k", target).returncode == 0

    def describe(self, tunnel: str, verbose: bool = True) -> str:
        return f"LaunchAgent Status: {'Installed' if self.is_enabled(tunnel) else 'Not installed'}"


# --------------------------------------------------------------------------- #
# Windows: Task Scheduler
# --------------------------------------------------------------------------- #

class TaskSchedulerBackend(AutostartBackend):
    name = "taskscheduler"

    @staticmethod
    def task(tunnel: str) -> str:
        return f"CloudflaredTunnel-{_file_safe(tunnel)}"

    @staticmethod
    def batch_file(tunnel: str) -> str:
        return os.path.join(paths.config_dir(), f"tunnel-{_file_safe(tunnel)}.bat")

    def install(self, tunnel: str, config_file: str, metrics_port: Optional[int] = None) -> bool:
        self.out(f"\n=== Setting up Windows Task for '{tunnel}' ===")
        path = cloudflared.find_binary() or "cloudflared"
        with open(self.batch_file(tunnel), "w") as f:
            f.write(f'@echo off\r\n{_bat_quote(path)} tunnel{_metrics(metrics_port)} --config {_bat_quote(config_file)} '
                    f'run {_bat_quote(tunnel)}\r\n')

        # The /tr value must itself contain quotes so paths with spaces work;
        # subprocess escapes them as \" which schtasks turns back into quotes.
        result = _quiet("schtasks", "/create", "/tn", self.task(tunnel),
                        "/tr", f'"{self.batch_file(tunnel)}"',
                        "/sc", "onlogon", "/rl", "limited", "/f")
        if result.returncode != 0:
            self.out(f"Failed to create scheduled task: {result.stderr.strip()}")
            return False
        processes.kill_tunnel(tunnel)
        _quiet("schtasks", "/run", "/tn", self.task(tunnel))
        self.out("\nTask scheduled successfully!")
        self.out(f"  Check status:  schtasks /query /tn {self.task(tunnel)}")
        return True

    def remove(self, tunnel: str) -> bool:
        self.out("Removing scheduled task...")
        _quiet("schtasks", "/end", "/tn", self.task(tunnel))
        if _quiet("schtasks", "/delete", "/tn", self.task(tunnel), "/f").returncode != 0:
            self.out("Failed to remove scheduled task")
            return False
        try:
            os.remove(self.batch_file(tunnel))
        except OSError:
            pass
        self.out("Scheduled task removed")
        return True

    def is_enabled(self, tunnel: str) -> bool:
        return _quiet("schtasks", "/query", "/tn", self.task(tunnel)).returncode == 0

    def start(self, tunnel: str) -> bool:
        return _quiet("schtasks", "/run", "/tn", self.task(tunnel)).returncode == 0

    def stop(self, tunnel: str):
        _quiet("schtasks", "/end", "/tn", self.task(tunnel))

    def restart(self, tunnel: str) -> bool:
        self.stop(tunnel)
        time.sleep(2)
        processes.kill_tunnel(tunnel)
        return self.start(tunnel)

    def describe(self, tunnel: str, verbose: bool = True) -> str:
        result = _quiet("schtasks", "/query", "/tn", self.task(tunnel))
        if result.returncode != 0:
            return "Task Scheduler Status: Not installed"
        return "Task Scheduler Status: " + ("Running" if "Running" in result.stdout else "Ready")


def get_backend(out: Output = print) -> Optional[AutostartBackend]:
    """The auto-start mechanism for this machine (None if unsupported)."""
    if sys.platform.startswith("linux"):
        return SystemdUserBackend(out) if SystemdUserBackend.available() else CronBackend(out)
    if sys.platform == "darwin":
        return LaunchAgentBackend(out)
    if sys.platform == "win32":
        return TaskSchedulerBackend(out)
    return None

