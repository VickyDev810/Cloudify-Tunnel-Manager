"""safety-check.sh: what can be tested without stopping anything on the machine running the tests.

Stopping and removing are tested in containers (tests/install/safety.sh, run by tests/test_install.py
with CLOUDIFY_INSTALL_TESTS=1), never here: --kill-all would stop the developer's own tunnels.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "safety-check.sh"
STANDIN = ROOT / "tests" / "install" / "standin"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash script for Linux and macOS")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(*args, **kwargs):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=120, **kwargs)


@pytest.fixture
def standins(tmp_path):
    """Start stand-ins called cloudflared / cloudify (they only sleep and answer on their metrics port)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("cloudflared", "cloudify"):
        (bindir / name).write_text((STANDIN / name).read_text())
        (bindir / name).chmod(0o755)
    started = []

    def start(name, *args, env=None):
        proc = subprocess.Popen([str(bindir / name), *args], env=env or os.environ, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started.append(proc)
        return proc

    yield start
    for proc in started:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
    for proc in started:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


def test_lists_every_kind_of_tunnel_and_never_shows_a_token(standins):
    quick_metrics, named_metrics, app = free_port(), free_port(), free_port()
    secret = f"SECRET-TUNNEL-TOKEN-{os.getpid()}"
    standins("cloudify", "-p", str(app), env=dict(os.environ, STANDIN_METRICS=f"127.0.0.1:{quick_metrics}"))
    standins("cloudflared", "tunnel", "--url", "ssh://127.0.0.1:2222")
    standins("cloudflared", "tunnel", "--metrics", f"127.0.0.1:{named_metrics}", "--config", "/tmp/x/config-web.yml",
             "run", "safety-test-web")
    standins("cloudflared", "tunnel", "run", "--token", secret)
    time.sleep(2)
    result = run()
    out = result.stdout
    assert result.returncode == 0, result.stderr
    # temporary tunnel: its public address comes from cloudflared itself (/quicktunnel)
    assert f"https://stand-in-{quick_metrics}.trycloudflare.com -> http://localhost:{app}" in out
    assert "started by: cloudify -p" in out
    assert "SSH tunnel -> ssh://127.0.0.1:2222" in out
    assert "tunnel safety-test-web, 4 Cloudflare connections" in out
    assert "(started with a token)" in out
    assert secret not in out + result.stderr


def test_kill_all_asks_before_taking_websites_offline(standins):
    tunnel = standins("cloudflared", "tunnel", "--config", "/tmp/x/config-web.yml", "run", "safety-test-confirm")
    time.sleep(1)
    # no terminal to ask in (new session, no stdin): it must refuse instead of stopping anything
    result = run("--kill-all", stdin=subprocess.DEVNULL, start_new_session=True)
    assert result.returncode != 0
    assert "run again with --yes" in result.stdout + result.stderr
    assert tunnel.poll() is None


def test_options():
    assert run("--help").returncode == 0
    assert "--kill" in run("--help").stdout
    assert run("--purge").returncode == 2  # only together with --remove
    assert run("--no-such-option").returncode == 2
