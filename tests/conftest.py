"""Shared fixtures.

Every test runs with an isolated $HOME and fake `cloudflared`, `systemctl`,
`crontab` and `loginctl` (tests/fakes) first on PATH, so nothing touches the
real Cloudflare account, systemd or crontab of the machine running the tests.
"""

import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKES = Path(__file__).resolve().parent / "fakes"
SETUP_CODE = "TESTS-SETUP"   # first-run setup code (normally random, printed by `cloudify serve`)


@pytest.fixture(autouse=True)
def _known_setup_code(monkeypatch):
    monkeypatch.setenv("CLOUDIFY_SETUP_CODE", SETUP_CODE)


def tunnel_pids(name, home=None):
    pattern = (rf"cloudflared.*tunnel.*--config {re.escape(str(home))}/\.cloudflared/config-{re.escape(name)}\.yml run {re.escape(name)}$"
               if home else rf"cloudflared.*tunnel.*run {re.escape(name)}$")
    out = subprocess.run(["pgrep", "-f", pattern],
                         capture_output=True, text=True).stdout
    return out.split()


def wait_for(predicate, timeout=8.0, interval=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def fake_cert(domain, account):
    token = {"zoneID": "zone-" + domain.replace(".", "-"), "accountID": account, "apiToken": "x", "fakeZone": domain}
    return ("-----BEGIN ARGO TUNNEL TOKEN-----\n" + base64.b64encode(json.dumps(token).encode()).decode()
            + "\n-----END ARGO TUNNEL TOKEN-----\n")


class Env:
    def __init__(self, home: Path, mode: str):
        self.home = home
        self.mode = mode
        self.vars = dict(
            os.environ,
            HOME=str(home),
            USER="tester",
            PYTHONPATH=os.pathsep.join([str(ROOT)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]),
            # fakes first; the test Python's folder so their `#!/usr/bin/env python3` works anywhere
            PATH=os.pathsep.join([str(FAKES), os.path.dirname(sys.executable), "/usr/bin", "/bin"]),
            CLOUDIFY_SETUP_CODE=SETUP_CODE,
        )
        self.vars.pop("FAKE_NO_SYSTEMD", None)
        # One fake "Cloudflare" per test, shared by every machine (HOME) in it
        self.vars.update(FAKE_CF_DB=str(home.parent / "cloudflare.json"), CLOUDIFY_OFFLINE="1",
                         FAKE_CF_LOGIN_DELAY="0.5",
                         FAKE_CF_ACCOUNTS="example.com=acct-a,shop.net=acct-a,other.org=acct-b",
                         # never reach the real Cloudflare API from tests (nothing listens on port 9)
                         CLOUDIFY_CF_API="http://127.0.0.1:9/client/v4")
        if mode == "cron":
            self.vars["FAKE_NO_SYSTEMD"] = "1"

    @property
    def cf(self) -> Path:
        return self.home / ".cloudflared"

    def cli(self, *args, stdin=""):
        result = subprocess.run([sys.executable, "-m", "cloudify", *args], input=stdin, capture_output=True,
                                text=True, env=self.vars, cwd=str(self.home), timeout=120)
        return result.returncode, result.stdout + result.stderr

    def login(self, domain="example.com", account="acct-a"):
        """Pretend `cloudflared tunnel login` already happened (as earlier versions did it)."""
        self.cf.mkdir(parents=True, exist_ok=True)
        (self.cf / "cert.pem").write_text(fake_cert(domain, account))

    def machine(self, name):
        """Another machine (HOME) using the same fake Cloudflare."""
        other = Env(self.home.parent / name, self.mode)
        other.home.mkdir(exist_ok=True)
        return other

    def account(self):
        path = Path(self.vars["FAKE_CF_DB"])
        return json.loads(path.read_text()) if path.exists() else {"tunnels": [], "dns": {}}

    def remote_tunnel(self, name):
        return next((t for t in self.account()["tunnels"] if t["name"] == name), None)

    def cleanup(self):
        """Stop every fake tunnel process started under this HOME."""
        out = subprocess.run(["pgrep", "-f", f"{FAKES}/cloudflared"], capture_output=True, text=True).stdout
        for pid in out.split():
            try:
                with open(f"/proc/{pid}/environ", "rb") as f:
                    if f"HOME={self.home}".encode() in f.read():
                        os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


@pytest.fixture(params=["systemd", "cron"])
def env(request, tmp_path):
    """Isolated environment; runs each test with systemd and with cron auto-start."""
    e = Env(tmp_path / "home", request.param)
    e.home.mkdir()
    yield e
    e.cleanup()


@pytest.fixture
def systemd_env(tmp_path):
    e = Env(tmp_path / "home", "systemd")
    e.home.mkdir()
    yield e
    e.cleanup()


@pytest.fixture
def cf_api(systemd_env):
    """Fake Cloudflare DNS API; accepts the token "dns-edit-token"."""
    import sys as _sys
    _sys.path.insert(0, str(FAKES))
    from cloudflare_api import FakeCloudflareApi
    api = FakeCloudflareApi(systemd_env.vars["FAKE_CF_DB"])
    systemd_env.vars["CLOUDIFY_CF_API"] = api.url
    yield api
    api.close()
