"""Real quick tunnel through Cloudflare (needs the real cloudflared + internet).

Opt-in:  CLOUDIFY_REAL_CLOUDFLARED=/path/to/cloudflared pytest tests/test_quick_tunnel.py
"""

import http.server
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

REAL = os.environ.get("CLOUDIFY_REAL_CLOUDFLARED")
pytestmark = pytest.mark.skipif(not REAL, reason="set CLOUDIFY_REAL_CLOUDFLARED to run")
ROOT = Path(__file__).resolve().parent.parent


def quick_pids(port):
    """cloudflared processes serving this test's port (`tunnel --no-autoupdate [--config FILE] --url ...`)."""
    # -ww: never cut long command lines (ps limits them to 80 columns in some environments, e.g. under pytest)
    out = subprocess.run(["ps", "-ww", "-eo", "pid,stat,args"], capture_output=True, text=True).stdout
    pattern = re.compile(rf"\btunnel --no-autoupdate (--config \S+ )?--url http://localhost:{port}$")
    return [l for l in out.splitlines() if pattern.search(l) and " Z" not in l]


def fetch(url, attempts=20):
    time.sleep(10)  # brand-new trycloudflare hostnames need a moment in DNS
    for _ in range(attempts):
        try:
            return urllib.request.urlopen(url, timeout=10).read().decode()
        except Exception:
            time.sleep(3)
    return None


def test_quick_tunnel_end_to_end(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("hello-cloudify")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(ROOT),
               PATH=f"{Path(REAL).parent}{os.pathsep}{os.environ['PATH']}")
    proc = subprocess.Popen([sys.executable, "-u", "-m", "cloudify", "-p", str(port)], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        for _ in range(100):  # the pattern must see the real process, or the final check proves nothing
            if quick_pids(port):
                break
            time.sleep(0.2)
        assert len(quick_pids(port)) == 1, [l for l in subprocess.run(
            ["ps", "-ww", "-eo", "pid,stat,args"], capture_output=True, text=True).stdout.splitlines() if str(port) in l]
        url = None
        for line in proc.stdout:
            match = re.search(r"Temporary tunnel active: (\S+)", line)
            if match:
                url = match.group(1)
                break
        assert url and url.endswith(".trycloudflare.com")
        assert fetch(url) == "hello-cloudify"

        listed = subprocess.run([sys.executable, "-m", "cloudify", "temp", "list"], env=env, capture_output=True, text=True)
        assert url in listed.stdout

        time.sleep(20)  # older versions froze once cloudflared's log pipe filled up
        assert fetch(url, attempts=5) == "hello-cloudify"

        os.killpg(proc.pid, signal.SIGINT)  # Ctrl+C
        proc.wait(timeout=20)
        assert not quick_pids(port)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        httpd.shutdown()
