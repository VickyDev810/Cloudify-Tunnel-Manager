"""Regression tests for the bugs found in the November 2026 code review (C1-C18, W1-W16)."""

import glob
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from conftest import ROOT, SETUP_CODE, tunnel_pids, wait_for


def state(env):
    return json.loads((env.cf / "tunnel_manager_state.json").read_text())


def create_remote(env, name, cert=None):
    args = ["cloudflared", "tunnel"] + (["--origincert", str(cert)] if cert else []) + ["create", name]
    assert subprocess.run(args, env=env.vars, capture_output=True).returncode == 0


def setup_web(env):
    env.login()
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart")
    assert rc == 0, out
    rc, out = env.cli("route", "add", "app.example.com", "3000", "-t", "web", stdin="n\n")
    assert rc == 0, out


# ------------------------------------------------------------------ C1/W1: concurrent state writes

def test_state_survives_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core.state import StateStore
    store = StateStore()
    for i in range(10):
        store.register(f"tunnel{i}", make_current=False)
    errors = []

    def worker(i):
        s = StateStore()
        for n in range(40):
            try:
                s.register(f"tunnel{i}")
                with s.locked():  # read-modify-write of one tunnel's routes, as the manager does it
                    routes = s.get(f"tunnel{i}").get("routes", []) + [{"domain": f"h{n}.example.com", "service": "x"}]
                    s.update(f"tunnel{i}", {"routes": routes})
            except Exception as e:  # pragma: no cover - the bug
                errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    tunnels = StateStore().tunnels()
    assert not errors and len(tunnels) == 10
    assert all(len(tunnels[f"tunnel{i}"]["routes"]) == 40 for i in range(4))

    # several processes at once
    code = ("import sys; from cloudify.core.state import StateStore\n"
            "s = StateStore()\n"
            "for n in range(30):\n"
            "    with s.locked():\n"
            "        r = s.get('tunnel9').get('routes', []) + [{'domain': sys.argv[1] + str(n), 'service': 'x'}]\n"
            "        s.update('tunnel9', {'routes': r})\n")
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]))
    procs = [subprocess.Popen([sys.executable, "-c", code, f"p{k}-"], env=env) for k in range(3)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    assert len(StateStore().get("tunnel9")["routes"]) == 90


def test_corrupt_state_is_kept_aside(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core.state import StateStore
    path = tmp_path / ".cloudflared" / "tunnel_manager_state.json"
    path.parent.mkdir()
    path.write_text('{"tunnels": {"web": ')
    assert StateStore().tunnels() == {}
    assert (path.parent / "tunnel_manager_state.json.corrupt").read_text() == '{"tunnels": {"web": '


# ------------------------------------------------------------------ C2: tunnels with the same name in two accounts

def test_same_tunnel_name_in_two_accounts(systemd_env):
    env = systemd_env
    env.login("example.com", "acct-a")
    create_remote(env, "home")  # account A, made by an older version (state has no account)
    (env.cf / "tunnel_manager_state.json").write_text(json.dumps(
        {"tunnels": {"home": {"routes": [], "auto_start": False}}, "current_tunnel": "home"}))
    rc, out = env.cli("account", "connect", "other.org")
    assert rc == 0, out
    b_cert = glob.glob(str(env.cf / "cloudify/certs/*.pem"))[0]
    create_remote(env, "home", b_cert)  # account B has a tunnel with the same name
    a_id = next(t["id"] for t in env.account()["tunnels"] if t["account"] == "acct-a")

    rc, out = env.cli("tunnel", "list")
    assert state(env)["tunnels"]["home"]["account_id"] == "acct-a", out  # cert.pem's account, the 1.x one
    rc, out = env.cli("tunnel", "delete", "home", "--force")
    assert rc == 0, out
    left = [(t["name"], t["account"]) for t in env.account()["tunnels"]]
    assert left == [("home", "acct-b")] and a_id not in json.dumps(left)

    # creating "web" in account B while account A's "web" is managed is refused, not "already exists"
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart", "--account", "acct-a")
    assert rc == 0, out
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart", "--account", "other.org")
    assert rc != 0 and "choose another name" in out, out

    # adopting a name that exists in both accounts asks which one
    create_remote(env, "shared")
    create_remote(env, "shared", b_cert)
    rc, out = env.cli("tunnel", "adopt", "shared")
    assert rc != 0 and "--account" in out, out
    rc, out = env.cli("tunnel", "adopt", "shared", "--account", "other.org")
    assert rc == 0 and state(env)["tunnels"]["shared"]["account_id"] == "acct-b", out


# ------------------------------------------------------------------ C3/C12/C13: account gone, tunnel gone

def test_delete_refuses_when_the_tunnels_account_is_not_connected(systemd_env):
    env = systemd_env
    env.login("example.com", "acct-a")
    rc, out = env.cli("account", "connect", "other.org")
    rc, out = env.cli("tunnel", "create", "blog", "--no-autostart", "--account", "other.org")
    assert rc == 0, out
    b_id = next(c["id"] for c in json.loads((env.cf / "cloudify/accounts.json").read_text())["connections"]
                if c["account_id"] == "acct-b")
    rc, out = env.cli("account", "remove", b_id)
    assert rc != 0 and "--force" in out, out  # tunnels depend on it: warn first
    rc, out = env.cli("account", "remove", b_id, "--force")
    assert rc == 0, out
    rc, out = env.cli("tunnel", "list")
    assert "Unknown" in out, out  # not "No (deleted)": its account just isn't connected here
    rc, out = env.cli("tunnel", "delete", "blog", "--force")
    assert rc != 0 and "not connected on this machine" in out, out
    assert env.remote_tunnel("blog") is not None


def test_delete_after_deletion_in_cloudflare_removes_credentials(systemd_env):
    env = systemd_env
    setup_web(env)
    tunnel_id = state(env)["tunnels"]["web"]["tunnel_id"]
    assert (env.cf / f"{tunnel_id}.json").exists()
    subprocess.run(["cloudflared", "tunnel", "delete", "-f", "web"], env=env.vars, capture_output=True)
    rc, out = env.cli("tunnel", "status", "web")
    assert rc == 0 and tunnel_id in out and "not running" in out.lower() or "running" in out.lower(), out
    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc == 0 and "no longer exists" in out, out
    assert not (env.cf / f"{tunnel_id}.json").exists()


# ------------------------------------------------------------------ C4/C5/C6/C11/C17: routes and adopted configs

def adopted_blog(env):
    env.login()
    create_remote(env, "blog")
    create_remote(env, "shop")
    blog_id = env.remote_tunnel("blog")["id"]
    (env.cf / "config.yml").write_text(f"""tunnel: {blog_id}
credentials-file: {env.cf}/{blog_id}.json
ingress:
  - hostname: MyBlog.example.com
    service: https://localhost:8443
    originRequest:
      noTLSVerify: true
  - hostname: plain.example.com
    service: http://localhost
  - service: http://localhost:8080
""")
    return blog_id


def test_adopt_uses_only_the_tunnels_own_config(systemd_env):
    env = systemd_env
    adopted_blog(env)
    rc, out = env.cli("tunnel", "adopt", "shop")
    assert rc == 0 and "no config found" in out, out  # config.yml is blog's
    assert not (env.cf / "config-shop.yml").exists()
    rc, out = env.cli("tunnel", "adopt", "blog")
    assert rc == 0 and "with 2 routes" in out, out


def test_routes_on_adopted_configs(systemd_env):
    env = systemd_env
    adopted_blog(env)
    assert env.cli("tunnel", "adopt", "blog")[0] == 0
    cfg = lambda: yaml.safe_load((env.cf / "config-blog.yml").read_text())["ingress"]

    rc, out = env.cli("route", "add", "new.example.com", "3000", "-t", "blog", stdin="n\n")
    assert rc == 0, out
    rules = cfg()
    assert rules[-1] == {"service": "http://localhost:8080"}  # the catch-all stays last
    assert {"hostname": "new.example.com", "service": "http://localhost:3000"} in rules

    rc, out = env.cli("route", "edit", "myblog.example.com", "-t", "blog", "--port", "9443")  # any case
    assert rc == 0, out
    rule = next(r for r in cfg() if r.get("hostname", "").lower() == "myblog.example.com")
    assert rule["service"] == "https://localhost:9443" and rule["originRequest"] == {"noTLSVerify": True}

    rc, out = env.cli("route", "edit", "plain.example.com", "-t", "blog", "--hostname", "plain2.example.com")
    assert rc == 0, out  # no port in the service: only the hostname changes
    assert {"hostname": "plain2.example.com", "service": "http://localhost"} in cfg()

    rc, out = env.cli("route", "remove", "MYBLOG.example.com", "-t", "blog")
    assert rc == 0 and not any(r.get("hostname", "").lower() == "myblog.example.com" for r in cfg()), out


def test_broken_yaml_is_contained(systemd_env):
    env = systemd_env
    setup_web(env)
    (env.cf / "config-web.yml").write_text("tunnel: x\ningress:\n  - hostname: a\n   service: broken\n")
    rc, out = env.cli("tunnel", "list")
    assert rc == 0 and "Traceback" not in out and "web" in out, out
    rc, out = env.cli("route", "add", "b.example.com", "3000", "-t", "web", stdin="n\n")
    assert rc != 0 and "can't be read" in out and "Traceback" not in out, out
    assert "broken" in (env.cf / "config-web.yml").read_text()  # left alone


# ------------------------------------------------------------------ C8/C15/C16: auto-start

def test_autostart_for_a_name_with_spaces(env):
    env.login()
    create_remote(env, "Old Tunnel")
    assert env.cli("tunnel", "adopt", "Old Tunnel")[0] == 0
    rc, out = env.cli("route", "add", "old.example.com", "3000", "-t", "Old Tunnel", stdin="n\n")
    assert rc == 0, out
    rc, out = env.cli("tunnel", "autostart", "Old Tunnel", "--enable")
    assert rc == 0, out

    def args_of_running():
        out = subprocess.run(["pgrep", "-f", "run Old Tunnel$"], capture_output=True, text=True).stdout.split()
        return [Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1] for pid in out]
    assert wait_for(lambda: args_of_running())
    argv = args_of_running()[0]
    assert argv[argv.index(b"run") + 1:] == [b"Old Tunnel"]  # one argument, not two

    if env.mode == "cron":
        crontab = (env.home / ".fakecrontab").read_text()
        line = next(l for l in crontab.splitlines() if "start-tunnel-" in l)
        assert len(shlex.split(line.replace("@reboot ", ""))) == 1, line  # the script path is one word
    else:
        unit = next((env.home / ".config/systemd/user").glob("cloudflared-tunnel-Old_Tunnel-*.service"))
        exec_start = next(l for l in unit.read_text().splitlines() if l.startswith("ExecStart="))
        words = shlex.split(exec_start.split("=", 1)[1])
        assert words[words.index("run") + 1:] == ["Old Tunnel"]


def test_disabling_autostart_keeps_the_tunnel_running(env):
    setup_web(env)
    assert env.cli("tunnel", "autostart", "web", "--enable")[0] == 0
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)
    rc, out = env.cli("tunnel", "autostart", "web", "--disable")
    assert rc == 0, out
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)
    rc, out = env.cli("tunnel", "autostart", "web", "--status")
    assert any(t in out for t in ("Not enabled", "Not installed", "Enabled: No")), out


def test_launchd_label_is_matched_exactly(monkeypatch):
    from cloudify.core import autostart
    listing = "PID\tStatus\tLabel\n123\t0\tcom.user.cloudflare.tunnel.web-ha\n"
    monkeypatch.setattr(autostart, "_quiet", lambda *a: subprocess.CompletedProcess(a, 0, listing, ""))
    backend = autostart.LaunchAgentBackend(lambda m: None)
    assert backend.is_enabled("web-ha") and not backend.is_enabled("web")


# ------------------------------------------------------------------ C9/W3: unknown names change nothing

def test_unknown_tunnel_names_are_not_recorded(env):
    setup_web(env)
    before = state(env)
    rc, out = env.cli("tunnel", "stop", "wbe")
    assert rc != 0 and "not managed" in out, out
    rc, out = env.cli("tunnel", "create", "has space", "--no-autostart")
    assert rc != 0
    rc, out = env.cli("tunnel", "use", "nosuch")
    assert rc != 0
    after = state(env)
    assert set(after["tunnels"]) == set(before["tunnels"]) and after["current_tunnel"] == before["current_tunnel"]


# ------------------------------------------------------------------ C10: replica export matches import

def test_export_refuses_routes_that_import_would_reject(systemd_env):
    env = systemd_env
    setup_web(env)
    cfg = yaml.safe_load((env.cf / "config-web.yml").read_text())
    cfg["ingress"].insert(0, {"hostname": "ssh.example.com", "service": "ssh://localhost:22"})
    (env.cf / "config-web.yml").write_text(yaml.safe_dump(cfg))
    rc, out = env.cli("replica", "export", str(env.home / "b.json"), "-t", "web", "--passphrase", "long enough pass")
    assert rc != 0 and "ssh://localhost:22" in out, out
    assert not (env.home / "b.json").exists()


# ------------------------------------------------------------------ C7/C14: quick tunnel bookkeeping

def test_stale_quick_tunnel_pid_is_never_killed(env):
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        env.cf.mkdir(parents=True, exist_ok=True)
        (env.cf / "tunnel_manager_state.json").write_text(json.dumps({"tunnels": {"temp-1-1": {
            "temp_tunnel": True, "temp_url": "https://old.trycloudflare.com", "temp_process_id": sleeper.pid}},
            "current_tunnel": None}))
        rc, out = env.cli("temp", "list")
        assert "old.trycloudflare.com" not in out, out
        env.cli("temp", "stop")
        time.sleep(0.5)
        assert sleeper.poll() is None  # still running
    finally:
        sleeper.kill()


def test_quick_tunnel_names_are_unique(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core import manager as manager_mod

    class Proc:
        def __init__(self, pid):
            self.pid = pid
    pids = iter([1001, 1002])
    monkeypatch.setattr(manager_mod.quick_tunnel, "start",
                        lambda host, port, **kw: (Proc(next(pids)), f"https://t{port}.trycloudflare.com", ""))
    monkeypatch.setattr(manager_mod.time, "time", lambda: 1700000000.0)
    m = manager_mod.TunnelManager(interactive=False, out=lambda s: None)
    m.create_temp_tunnel(3000)
    m.create_temp_tunnel(4000)
    temps = [i for i in m.state.tunnels().values() if i.get("temp_tunnel")]
    assert len(temps) == 2


# ------------------------------------------------------------------ C18/W16 and other CLI edges

def test_ssh_flag_without_mode(env):
    rc, out = env.cli("-ssh")
    assert rc != 0 and "requires an argument" in out and "Traceback" not in out, out


# ------------------------------------------------------------------ server: W3, W4, W11, W12

def test_api_unknown_tunnel_404_and_login_conflict(systemd_env, monkeypatch):
    from fastapi.testclient import TestClient
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "FAKE_CF_ACCOUNTS", "CLOUDIFY_CF_API"):
        if key in systemd_env.vars:
            monkeypatch.setenv(key, systemd_env.vars[key])
    monkeypatch.chdir(systemd_env.home)
    from cloudify.server import app as app_mod, auth, routes
    monkeypatch.setattr(auth, "users", auth.UserStore())   # fresh users.json under this HOME
    monkeypatch.setattr(auth, "tokens", auth.TokenStore())
    monkeypatch.setattr(routes, "auth", auth)
    client = TestClient(app_mod.app)
    assert client.get("/healthz").json() == {"status": "ok"}
    client.post("/auth/register", json={"username": "admin", "password": "secret123", "setup_code": SETUP_CODE})
    token = client.post("/auth/login", data={"username": "admin", "password": "secret123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/tunnel/status", params={"tunnel_name": "typo"}, headers=h).status_code == 404
    assert client.post("/tunnel/stop", json={"tunnel_name": "nosuch"}, headers=h).status_code == 404
    assert "typo" not in routes.manager().state.tunnels()
    assert "has_tunnels" not in client.get("/setup/check").json()

    assert routes._login_lock.acquire(blocking=False)  # a login is running
    routes._login_domain["value"] = "example.com"
    try:
        r = client.post("/accounts/connect", json={"domain": "shop.net"}, headers=h)
        assert r.status_code == 409 and "example.com" in r.json()["detail"]
    finally:
        routes._login_domain["value"] = None
        routes._login_lock.release()


def test_latest_version_failures_are_cached(monkeypatch):
    from cloudify.server import routes
    calls = []
    monkeypatch.setattr(routes.cloudflared, "latest_version", lambda timeout=5: calls.append(1))
    monkeypatch.setitem(routes._latest_cache, "checked", 0)
    monkeypatch.setitem(routes._latest_cache, "version", None)
    for _ in range(3):
        routes._latest_cloudflared()
    assert len(calls) == 1


# ------------------------------------------------------------------ SSH: W6, W8, W9, W13, access-code payload

def test_access_code_payload_is_validated():
    from cloudify.ssh import credentials as c
    creds = c.generate()
    good = c.open_access_code(c.make_access_code(creds, "abc.trycloudflare.com", "ai", time.time() + 60), creds.password)
    assert good["host"] == "abc.trycloudflare.com"
    for host in ("x.trycloudflare.com -oProxyCommand=id", "-oProxyCommand=id", "a;id", "a b", "x\nevil"):
        with pytest.raises(c.AccessCodeError, match="invalid connection details"):
            c.open_access_code(c.make_access_code(creds, host, "ai", time.time() + 60), creds.password)
    from cloudify.ssh.remote import _login_name
    assert _login_name("ai") == "ai" and _login_name("-oProxyCommand=x") == "cloudify"


def test_quick_tunnel_start_can_be_cancelled(tmp_path, monkeypatch):
    fake = tmp_path / "cloudflared"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    from cloudify.core import cloudflared, quick_tunnel
    monkeypatch.setattr(cloudflared, "binary", lambda: str(fake))
    cancel, seen = threading.Event(), []
    threading.Timer(0.5, cancel.set).start()
    started = time.time()
    process, url, error = quick_tunnel.start("127.0.0.1", 1, timeout=30, cancel=cancel, on_process=seen.append)
    assert url is None and error == "Cancelled" and time.time() - started < 5
    assert seen and seen[0].wait(timeout=5) is not None  # the process was stopped


def test_ssh_host_without_cloudflared(tmp_path):
    env = dict(os.environ, PATH="/usr/bin:/bin", HOME=str(tmp_path),
               PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]))
    if any(os.path.exists(p) for p in ("/usr/local/bin/cloudflared", "/usr/bin/cloudflared")):
        pytest.skip("a system cloudflared is installed")
    r = subprocess.run([sys.executable, "-m", "cloudify", "ssh", "host"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 1 and "cloudflared is not installed" in r.stdout + r.stderr
    assert "Traceback" not in r.stdout + r.stderr


# ================================================================== security audit run-1 fixes

def _users_file(home):
    path = home / ".cloudflared" / "cloudify" / "users.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("case", ["world-writable", "symlink", "bad-hash", "empty-name", "good"])
def test_legacy_users_json_is_adopted_only_when_trustworthy(tmp_path, monkeypatch, case):
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    record = {"username": "mallory", "hashed_password": "$2b$12$" + "a" * 53, "is_admin": True}
    users = {"mallory": record}
    if case == "bad-hash":
        users = {"mallory": dict(record, hashed_password="plain")}
    if case == "empty-name":
        users = {"": record}
    source = work / "users.json"
    if case == "symlink":
        real = tmp_path / "elsewhere.json"
        real.write_text(json.dumps(users))
        real.chmod(0o600)
        source.symlink_to(real)
    else:
        source.write_text(json.dumps(users))
        source.chmod(0o666 if case == "world-writable" else 0o600)
    from cloudify.server.auth import UserStore
    store = UserStore()
    target = Path(store.path)
    if case == "good":
        assert target.exists() and (target.stat().st_mode & 0o777) == 0o600
        assert store.load() == users
    else:
        assert not target.exists() and store.count() == 0


def test_unreadable_user_list_fails_closed(systemd_env, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HOME", str(systemd_env.home))
    _users_file(systemd_env.home).write_text('{"admin": ')  # damaged
    from cloudify.server import app as app_mod
    client = TestClient(app_mod.app)
    assert client.get("/setup/check").status_code == 503
    r = client.post("/auth/register", json={"username": "eve", "password": "secret123"})
    assert r.status_code == 503 and "can't be read" in r.json()["detail"]
    assert _users_file(systemd_env.home).read_text() == '{"admin": '  # not overwritten


def test_security_headers_docs_and_host_check(systemd_env, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HOME", str(systemd_env.home))
    from cloudify.server import app as app_mod
    client = TestClient(app_mod.app)
    try:
        r = client.get("/")
        assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert "script-src 'self';" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff"
        assert "<script>" not in r.text  # no inline script: the CSP allows this origin's files only
        assert client.get("/api/docs").status_code == 404 and client.get("/openapi.json").status_code == 404

        app_mod.configure(allowed_hosts=["dash.example.com", "testserver"], api_docs=True)
        assert client.get("/api/docs").status_code == 200 and client.get("/openapi.json").status_code == 200
        for host, ok in (("127.0.0.1:8765", True), ("localhost:8765", True), ("[::1]:8765", True),
                         ("192.168.1.20:8765", True), ("dash.example.com", True),
                         ("rebind.attacker.example:8765", False), ("evil.com", False)):
            status = client.get("/healthz", headers={"Host": host}).status_code
            assert (status == 200) == ok, (host, status)
    finally:
        app_mod.configure(check_host=False)


def test_serve_refuses_plain_http_on_the_network(env):
    """Only in secure mode: by default the dashboard is open to the network (see test_open_by_default...)."""
    rc, out = env.cli("serve", "--secure", "--host", "0.0.0.0", "--serve-port", "18799")
    assert rc != 0 and "Refusing to serve plain HTTP" in out, out
    env.vars["CLOUDIFY_SECURE"] = "1"
    rc, out = env.cli("serve", "--host", "0.0.0.0", "--serve-port", "18799")
    assert rc != 0 and "Refusing to serve plain HTTP" in out, out


def _non_loopback_address():
    """This machine's address on its default route, or None (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
            address = s.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") else address


def test_open_by_default_and_secure_on_request(systemd_env, tmp_path):
    """`cloudify serve` answers on every interface under any name (a server's public IP, a domain, a quick
    tunnel). --secure, or CLOUDIFY_SECURE=1, keeps it on this machine and to known names."""
    import urllib.error
    import urllib.request
    env = systemd_env

    def get(port, host, address="127.0.0.1"):
        request = urllib.request.Request(f"http://{address}:{port}/healthz", headers={"Host": host})
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, ""
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except OSError as e:
            return None, str(e)

    def serve(*args, **extra_env):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        log = tmp_path / f"serve-{port}.log"
        proc = subprocess.Popen([sys.executable, "-m", "cloudify", "serve", "--serve-port", str(port), *args],
                                env=dict(env.vars, **extra_env), cwd=str(env.home),
                                stdout=log.open("w"), stderr=subprocess.STDOUT)
        assert wait_for(lambda: get(port, "localhost")[0] == 200, timeout=20), log.read_text()
        return proc, port, log

    lan = _non_loopback_address()
    proc, port, log = serve()
    try:
        for name in ("closest-approaches-durham-acts.trycloudflare.com", "dash.example.com", "203.0.113.7:8765"):
            assert get(port, name)[0] == 200, name
        if lan:
            assert get(port, lan, address=lan)[0] == 200  # every interface
        out = log.read_text()
        assert "Open to the network" in out and "--secure" in out, out
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    for args, extra in ((("--secure",), {}), ((), {"CLOUDIFY_SECURE": "1"})):
        proc, port, log = serve(*args, **extra)
        try:
            status, body = get(port, "closest-approaches-durham-acts.trycloudflare.com")
            assert status == 400 and "secure mode" in body, (status, body)
            assert get(port, "127.0.0.1:%d" % port)[0] == 200
            if lan:
                assert get(port, lan, address=lan)[0] is None  # this machine only: nothing listens there
            assert "Secure mode" in log.read_text()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    proc, port, log = serve("--secure", CLOUDIFY_ALLOWED_HOSTS="*.trycloudflare.com")
    try:
        assert get(port, "closest-approaches-durham-acts.trycloudflare.com")[0] == 200
        assert get(port, "dash.example.com")[0] == 400
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_allowed_host_patterns(systemd_env, monkeypatch):
    """`--allowed-host '*.trycloudflare.com'` opens the dashboard under every quick tunnel's name, and only there."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("HOME", str(systemd_env.home))
    from cloudify.server import app as app_mod
    client = TestClient(app_mod.app)
    name = "closest-approaches-durham-acts.trycloudflare.com"
    try:
        app_mod.configure(allowed_hosts=["*.trycloudflare.com"])
        for host, ok in ((name, True), ("a.b.trycloudflare.com:443", True), ("trycloudflare.com", False),
                         ("x.trycloudflare.com.evil.com", False), ("eviltrycloudflare.com", False), ("evil.com", False)):
            status = client.get("/healthz", headers={"Host": host}).status_code
            assert (status == 200) == ok, (host, status)
        # the dashboard's own page may send changes; a page on another quick tunnel may not
        assert app_mod.origin_allowed(f"https://{name}", name)
        assert not app_mod.origin_allowed("https://attacker.trycloudflare.com", name)
        r = client.post("/auth/login", data={"username": "x", "password": "y"},
                        headers={"Host": name, "Origin": "https://attacker.trycloudflare.com"})
        assert r.status_code == 403, r.text
    finally:
        app_mod.configure(check_host=False)
    for bad in ("*", "*.com", "a.*.example.com", "*example.com", "**.example.com"):
        assert app_mod.allowed_host_error(bad), bad
    for good in ("*.trycloudflare.com", "dash.example.com", "*.Example.COM."):
        assert app_mod.allowed_host_error(good) is None, good


def test_serve_refuses_too_broad_host_patterns(env):
    rc, out = env.cli("serve", "--allowed-host", "*.com", "--serve-port", "18798")
    assert rc != 0 and "isn't a valid host pattern" in out, out
    env.vars["CLOUDIFY_ALLOWED_HOSTS"] = "dash.example.com,*"
    rc, out = env.cli("serve", "--serve-port", "18798")
    assert rc != 0 and "isn't a valid host pattern" in out, out


def test_ssh_server_disables_gss_auth():
    import asyncio
    from cloudify.ssh import credentials
    from cloudify.ssh.server import SessionServer

    async def check():
        server = SessionServer(credentials.generate(), lambda *a: None)
        await server.start()
        try:
            opts = server.server._options
            return opts.gss_auth, opts.gss_kex
        finally:
            await server.close()
    assert asyncio.run(check()) == (False, False)


def test_names_and_api_base_are_restricted(monkeypatch):
    from cloudify.core import dns_api
    from cloudify.core.validation import is_safe_tunnel_name
    assert not is_safe_tunnel_name("--loglevel=debug") and not is_safe_tunnel_name("-x")
    assert is_safe_tunnel_name("Old Tunnel")
    monkeypatch.setenv("CLOUDIFY_CF_API", "http://attacker.example/client/v4")
    with pytest.raises(dns_api.DnsApiError):
        dns_api._base()
    monkeypatch.setenv("CLOUDIFY_CF_API", "http://127.0.0.1:9/client/v4")
    assert dns_api._base().startswith("http://127.0.0.1")


def test_login_status_never_holds_the_api_token(systemd_env, cf_api):
    env = systemd_env
    assert env.cli("account", "connect", "example.com")[0] == 0
    accounts = json.loads((env.cf / "cloudify/accounts.json").read_text())["connections"]
    cid = next(c["id"] for c in accounts if c["domain"] == "example.com")
    rc, out = env.cli("account", "token", cid, stdin="dns-edit-token\n")
    assert rc == 0, out
    assert env.cli("account", "connect", "example.com")[0] == 0  # same zone again: certificate refresh
    status = env.cf / "cloudify/status.json"
    assert "dns-edit-token" not in status.read_text() and (status.stat().st_mode & 0o777) == 0o600
    for d in (env.cf, env.cf / "cloudify", env.cf / "cloudify/certs"):
        assert (d.stat().st_mode & 0o777) == 0o700, d
    for cert in (env.cf / "cloudify/certs").glob("*.pem"):
        assert (cert.stat().st_mode & 0o777) == 0o600


def test_process_lookup_is_limited_to_this_user(monkeypatch):
    from cloudify.core import processes
    seen = []
    monkeypatch.setattr(processes.subprocess, "run",
                        lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 1, "", ""))
    processes.find_pids("cloudflared.*run web$")
    assert seen and seen[0][:3] == ["pgrep", "-u", str(os.getuid())]


def test_metrics_port_held_by_another_account_is_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core import cloudflared, manager as manager_mod
    monkeypatch.setattr(cloudflared, "listener_uids", lambda port: {os.getuid() + 1} if port == 20300 else set())
    assert cloudflared.local_health(20300) is None
    m = manager_mod.TunnelManager(interactive=False, out=lambda s: None)
    m.state.register("web", make_current=False)
    m.state.update("web", {"metrics_port": 20300})
    assert m.metrics_port("web") not in (0, 20300)


def test_replica_import_drops_unsafe_rule_options(systemd_env, monkeypatch):
    env = systemd_env
    setup_web(env)
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "CLOUDIFY_CF_API", "FAKE_CF_ACCOUNTS"):
        monkeypatch.setenv(key, env.vars[key])
    from cloudify.core import replica
    from cloudify.core.manager import TunnelManager
    m = TunnelManager(interactive=False, out=lambda s: None)
    payload = {"source": "author", "created_at": "now", "tunnels": [{
        "name": "jump", "tunnel_id": "11111111-2222-3333-4444-555555555555", "account_id": "acct-a",
        "credentials": {"AccountTag": "acct-a", "TunnelSecret": "c2VjcmV0", "TunnelID": "11111111-2222-3333-4444-555555555555"},
        "ingress": [{"hostname": "jump.example.com", "service": "http://localhost:8080",
                     "originRequest": {"bastionMode": True, "proxyType": "socks", "noTLSVerify": True}},
                    {"service": "http_status:404"}]}]}
    bundle = replica.encrypt(payload, "long enough passphrase")
    replica.import_bundle(m, bundle, "long enough passphrase", autostart=False)
    rule = yaml.safe_load((env.cf / "config-jump.yml").read_text())["ingress"][0]
    assert rule == {"hostname": "jump.example.com", "service": "http://localhost:8080",
                    "originRequest": {"noTLSVerify": True}}
    assert (oct((env.cf / "11111111-2222-3333-4444-555555555555.json").stat().st_mode & 0o777)) == oct(0o600)

    # a bundle reusing the ID of a tunnel this machine runs under another name is refused
    web_id = state(env)["tunnels"]["web"]["tunnel_id"]
    payload["tunnels"][0].update(name="other", tunnel_id=web_id,
                                 credentials={"TunnelID": web_id, "TunnelSecret": "eA==", "AccountTag": "acct-a"})
    with pytest.raises(replica.ReplicaError, match="already has tunnel"):
        replica.import_bundle(m, replica.encrypt(payload, "long enough passphrase"), "long enough passphrase")
    with pytest.raises(replica.ReplicaError):  # trailing newline after an ID
        replica._check_tunnel(dict(payload["tunnels"][0], tunnel_id=web_id + "\n"))


def test_replica_names_and_summary_are_checked(systemd_env, monkeypatch):
    env = systemd_env
    setup_web(env)
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "CLOUDIFY_CF_API", "FAKE_CF_ACCOUNTS"):
        monkeypatch.setenv(key, env.vars[key])
    from cloudify.core import replica
    from cloudify.core.manager import TunnelManager
    m = TunnelManager(interactive=False, out=lambda s: None)
    tid = "11111111-2222-3333-4444-555555555555"

    def bundle_for(name):
        return replica.encrypt({"source": "author", "created_at": "now", "tunnels": [{
            "name": name, "tunnel_id": tid, "account_id": "acct-a",
            "credentials": {"AccountTag": "acct-a", "TunnelSecret": "c2VjcmV0", "TunnelID": tid},
            "ingress": [{"hostname": "r.example.com", "service": "http://localhost:8080"},
                        {"service": "http_status:404"}]}]}, "long enough passphrase")

    # names from a replica file become new tunnels here: the strict rule applies
    for bad in ("replica one", "-x", "web\n", 7):
        with pytest.raises(replica.ReplicaError):
            replica.import_bundle(m, bundle_for(bad), "long enough passphrase")
    assert not (env.cf / f"{tid}.json").exists() and not list(env.cf.glob("config-replica*"))
    assert (env.home / ".local/bin").exists() is False or not list((env.home / ".local/bin").glob("start-tunnel-replica*"))

    # the readable summary must describe the encrypted contents
    tampered = bundle_for("rep")
    tampered["tunnels"] = [{"name": "harmless", "hostnames": ["harmless.example.com"]}]
    with pytest.raises(replica.ReplicaError, match="modified"):
        replica.import_bundle(m, tampered, "long enough passphrase")
    assert replica.import_bundle(m, bundle_for("rep"), "long enough passphrase", autostart=False)

    # export refuses names that import would refuse, with a way out
    m.state.register("Old Tunnel", make_current=False)
    with pytest.raises(replica.ReplicaError, match="replica split"):
        replica.export(m, ["Old Tunnel"], "long enough passphrase")


def test_validation_patterns_reject_a_trailing_newline():
    from cloudify.core import validation
    from cloudify.ssh import remote
    assert validation.is_valid_tunnel_name("web") and not validation.is_valid_tunnel_name("web\n")
    assert validation.is_valid_domain("a.example.com") and not validation.is_valid_domain("a.example.com\n")
    assert validation.is_valid_service_host("db") and not validation.is_valid_service_host("db\n")
    assert not validation.is_safe_tunnel_name(None) and not validation.is_valid_tunnel_name(["x"])
    assert remote._login_name("ai") == "ai" and remote._login_name("ai\n") == "cloudify"


def test_installer_python_helpers_are_isolated():
    import re as _re
    text = (ROOT / "install.sh").read_text()
    calls = _re.findall(r'"\$PY" (-\S*)', text)
    assert calls and all(flag == "-I" or flag.startswith("--version") for flag in calls), calls
    assert "\ncd / ||" in text  # works from / after resolving the user's paths


def test_legacy_install_links_always_download(tmp_path):
    (tmp_path / "install.sh").write_text("echo PLANTED-INSTALLER-RAN\n")
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text("#!/bin/sh\necho 'echo OFFICIAL-INSTALLER \"$@\"'\n")
    (bin_dir / "curl").chmod(0o755)
    for shim in ("final.sh", "mac.sh"):
        (work / shim).write_text((ROOT / "install_scripts" / shim).read_text())
        r = subprocess.run(["bash", shim, "--update"], cwd=work, capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin"))
        assert "PLANTED-INSTALLER-RAN" not in r.stdout and "OFFICIAL-INSTALLER --update" in r.stdout, r.stdout + r.stderr


def test_ssh_remote_restart_ignores_a_cloudify_folder_in_the_working_directory(tmp_path):
    planted = tmp_path / "cloudify"
    planted.mkdir()
    (planted / "__init__.py").write_text("print('PLANTED-PACKAGE-IMPORTED')\n")
    (planted / "__main__.py").write_text("print('PLANTED-PACKAGE-IMPORTED')\n")
    launcher = "import sys; sys.path.pop(0); sys.argv[0] = 'cloudify'; from cloudify.cli import cli; cli()"
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]))
    r = subprocess.run([sys.executable, "-c", launcher, "ssh", "remote", "--code", "cfyssh1.xx.00000000",
                        "--password", "x"], cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env)
    assert "PLANTED-PACKAGE-IMPORTED" not in r.stdout + r.stderr
    assert "access code" in (r.stdout + r.stderr).lower(), r.stdout + r.stderr


def test_named_connection_cannot_be_relabelled(systemd_env):
    env = systemd_env
    assert env.cli("account", "connect", "example.com")[0] == 0
    accounts = json.loads((env.cf / "cloudify/accounts.json").read_text())["connections"]
    cid = next(c["id"] for c in accounts if c["domain"] == "example.com")
    rc, out = env.cli("account", "domain", cid, "other.org")
    assert rc != 0 and "already example.com" in out, out
    rc, out = env.cli("account", "connect", "bad domain;")
    assert rc != 0 and "like example.com" in out, out


def test_find_binary_ignores_the_current_folder(tmp_path, monkeypatch):
    from cloudify.core import cloudflared
    planted = tmp_path / "cloudflared"
    planted.write_text("#!/bin/sh\necho planted\n")
    planted.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", os.pathsep + "relative-dir" + os.pathsep + str(tmp_path / "empty"))
    found = cloudflared.find_binary()  # an empty or relative PATH entry means the current folder: skip it
    assert found is None or (os.path.isabs(found) and Path(found) != planted), found
    good = tmp_path / "bin"
    good.mkdir()
    (good / "cloudflared").write_text("#!/bin/sh\n")
    (good / "cloudflared").chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep + str(good))
    assert cloudflared.find_binary() == str(good / "cloudflared")


def test_cloudflared_update_runs_one_at_a_time(monkeypatch):
    from cloudify.core import cloudflared
    monkeypatch.setattr(cloudflared, "find_binary", lambda: "/bin/true")
    assert cloudflared._update_lock.acquire(blocking=False)
    try:
        ok, message = cloudflared.self_update()
        assert not ok and "already running" in message
    finally:
        cloudflared._update_lock.release()


# ------------------------------------------------------------------ security audit, wave 2

def _api(systemd_env, monkeypatch):
    from fastapi.testclient import TestClient
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "FAKE_CF_ACCOUNTS", "CLOUDIFY_CF_API"):
        if key in systemd_env.vars:
            monkeypatch.setenv(key, systemd_env.vars[key])
    monkeypatch.chdir(systemd_env.home)
    from cloudify.server import app as app_mod, auth, routes
    monkeypatch.setattr(auth, "users", auth.UserStore())
    monkeypatch.setattr(auth, "tokens", auth.TokenStore())
    monkeypatch.setattr(routes, "auth", auth)
    return TestClient(app_mod.app), auth


def _token(client, username, password):
    r = client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_first_account_needs_the_setup_code(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    body = {"username": "admin", "password": "secret12"}
    for code in (None, "", "WRONG-CODE", "TESTS-SETU"):
        r = client.post("/auth/register", json=dict(body, setup_code=code))
        assert r.status_code == 403 and "setup code" in r.json()["detail"], (code, r.text)
    assert client.get("/setup/check").json()["needs_setup"]
    assert client.post("/auth/register", json=dict(body, setup_code="tests setup")).status_code == 200
    # afterwards the code is useless: only signed-in users add accounts
    assert client.post("/auth/register", json={"username": "eve", "password": "secret12",
                                               "setup_code": SETUP_CODE}).status_code == 403

    # without CLOUDIFY_SETUP_CODE the code is random, readable and stable for the process
    monkeypatch.delenv("CLOUDIFY_SETUP_CODE")
    monkeypatch.setitem(auth._setup, "code", None)
    code = auth.setup_code()
    assert __import__("re").fullmatch(r"[A-HJ-NP-Z2-9]{5}-[A-HJ-NP-Z2-9]{5}", code) and auth.setup_code() == code
    assert auth.setup_code_matches(code.lower()) and not auth.setup_code_matches("") and not auth.setup_code_matches(None)


def test_serve_prints_the_setup_code_only_while_there_is_no_account(systemd_env):
    env = systemd_env
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    def first_lines():
        proc = subprocess.Popen([sys.executable, "-m", "cloudify", "serve", "--serve-port", str(port), "--host", "127.0.0.1"],
                                env=env.vars, cwd=str(env.home), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            lines = []
            while not any("running at" in l for l in lines):
                line = proc.stdout.readline()
                if not line:
                    break
                lines.append(line)
            return "".join(lines)
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    out = first_lines()
    assert f"Setup code: {SETUP_CODE}" in out, out
    users = env.cf / "cloudify" / "users.json"
    users.parent.mkdir(parents=True, exist_ok=True)
    users.write_text(json.dumps({"admin": {"username": "admin", "hashed_password": "$2b$04$" + "x" * 53}}))
    assert "Setup code" not in first_lines()


def test_tokens_from_logins_in_flight_die_with_the_password(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    client.post("/auth/register", json={"username": "alice", "password": "oldpass1", "setup_code": SETUP_CODE})
    owner = _token(client, "alice", "oldpass1")
    old_epoch = auth.session_epoch(auth.users.get("alice"))
    other = _token(client, "alice", "oldpass1")

    r = client.post("/auth/change-password", json={"current_password": "oldpass1", "new_password": "newpass1"}, headers=owner)
    assert r.status_code == 200, r.text
    # a login that verified the old password just before the change finishes after it
    late = {"Authorization": "Bearer " + auth.tokens.issue("alice", old_epoch)}
    assert client.get("/auth/me", headers=late).status_code == 401
    assert client.get("/auth/me", headers=other).status_code == 401
    assert client.get("/auth/me", headers=owner).status_code == 200      # the caller stays signed in
    assert client.post("/auth/login", data={"username": "alice", "password": "oldpass1"}).status_code == 401

    # a deleted account's tokens never come back with a new account of the same name
    client.post("/auth/register", json={"username": "bob", "password": "bobpass1"}, headers=owner)
    bob = _token(client, "bob", "bobpass1")
    bob_late = {"Authorization": "Bearer " + auth.tokens.issue("bob", auth.session_epoch(auth.users.get("bob")))}
    assert client.post("/auth/users/delete", json={"username": "bob"}, headers=owner).status_code == 200
    client.post("/auth/register", json={"username": "bob", "password": "otherpw9"}, headers=owner)
    assert client.get("/auth/me", headers=bob).status_code == 401
    assert client.get("/auth/me", headers=bob_late).status_code == 401


def test_password_change_against_a_real_login_loop(systemd_env, monkeypatch):
    """The audit's reproduction: an attacker keeps logging in while the owner changes the password."""
    client, auth = _api(systemd_env, monkeypatch)
    client.post("/auth/register", json={"username": "alice", "password": "oldpass1", "setup_code": SETUP_CODE})
    owner = _token(client, "alice", "oldpass1")
    stolen, stop = [], threading.Event()

    def attacker():
        from fastapi.testclient import TestClient
        from cloudify.server import app as app_mod
        c = TestClient(app_mod.app)
        while not stop.is_set():
            r = c.post("/auth/login", data={"username": "alice", "password": "oldpass1"})
            if r.status_code == 200:
                stolen.append(r.json()["access_token"])

    t = threading.Thread(target=attacker)
    t.start()
    try:
        time.sleep(0.6)
        r = client.post("/auth/change-password", json={"current_password": "oldpass1", "new_password": "newpass1"},
                        headers=owner)
        assert r.status_code == 200
        time.sleep(0.6)
    finally:
        stop.set()
        t.join(timeout=30)
    assert stolen
    assert all(client.get("/auth/me", headers={"Authorization": f"Bearer {s}"}).status_code == 401 for s in stolen)


def test_unknown_user_login_costs_a_bcrypt_check(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    seen = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda pw, hashed: seen.append(hashed) or real(pw, hashed))
    assert client.post("/auth/login", data={"username": "nobody", "password": "whatever1"}).status_code == 401
    assert seen == [auth.dummy_hash()]


def test_token_store_is_bounded_per_user():
    from cloudify.server.auth import MAX_TOKENS_PER_USER, TokenStore
    store = TokenStore()
    tokens = [store.issue("alice", "e") for _ in range(MAX_TOKENS_PER_USER + 10)]
    alive = [t for t in tokens if store.entry(t)]
    assert len(alive) == MAX_TOKENS_PER_USER and tokens[-1] in alive and tokens[0] not in alive


def test_api_docs_load_a_pinned_checked_swagger_ui(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    from cloudify.server import app as app_mod
    try:
        app_mod.configure(api_docs=True, check_host=False)
        r = client.get("/api/docs")
        assert r.status_code == 200 and r.text.count('integrity="sha384-') == 2 and "swagger-ui-dist@5.35.0/" in r.text
        assert "@5/" not in r.text and "<script>" not in r.text  # exact version, no inline script
        csp = r.headers["Content-Security-Policy"]
        script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
        assert "unsafe-inline" not in script_src and "swagger-ui-dist@5.35.0/" in script_src
        init = client.get("/api/docs/init.js")
        assert init.status_code == 200 and "SwaggerUIBundle" in init.text
    finally:
        app_mod.configure(check_host=False)
    assert client.get("/api/docs/init.js").status_code == 404


def test_disconnected_account_never_falls_back_to_another_certificate(systemd_env, monkeypatch):
    env = systemd_env
    assert env.cli("account", "connect", "other.org")[0] == 0      # acct-b: its certificate becomes cert.pem
    assert env.cli("account", "connect", "example.com")[0] == 0    # acct-a
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart", "--account", "acct-a")
    assert rc == 0, out
    connections = json.loads((env.cf / "cloudify/accounts.json").read_text())["connections"]
    cid = next(c["id"] for c in connections if c["domain"] == "example.com")
    assert env.cli("account", "remove", cid, "--force")[0] == 0
    st = state(env)
    st["tunnels"]["web"].pop("tunnel_id", None)                   # as recorded by an older version
    (env.cf / "tunnel_manager_state.json").write_text(json.dumps(st))

    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "FAKE_CF_ACCOUNTS", "CLOUDIFY_CF_API"):
        monkeypatch.setenv(key, env.vars[key])
    from cloudify.core.manager import AccountNotConnected, TunnelManager
    m = TunnelManager(interactive=False, out=lambda s: None)
    with pytest.raises(AccountNotConnected):
        m._cert_for_tunnel("web")
    assert m._tunnel_id("web") is None                             # not looked up in acct-b
    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc != 0 and "not connected on this machine" in out, out


def test_dns_cleanup_off_means_no_api_calls_and_bad_certs_are_ignored(tmp_path):
    from cloudify.core import accounts
    store = accounts.AccountStore.__new__(accounts.AccountStore)
    cert = tmp_path / "cert.pem"
    cert.write_text(fake_cert_text({"zoneID": "z", "accountID": "a", "apiToken": "cert-token"}))
    assert store.dns_tokens({"cert": str(cert)}) == []
    assert store.dns_tokens({"cert": str(cert), "api_token": "mine"}) == ["mine", "cert-token"]
    cert.write_text(fake_cert_text(["not", "an", "object"]))
    assert accounts.parse_cert(str(cert)) == {}


def fake_cert_text(token):
    import base64 as _b64
    return ("-----BEGIN ARGO TUNNEL TOKEN-----\n" + _b64.b64encode(json.dumps(token).encode()).decode()
            + "\n-----END ARGO TUNNEL TOKEN-----\n")


def test_cross_site_requests_that_change_something_are_refused(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    body = {"username": "admin", "password": "secret12", "setup_code": SETUP_CODE}
    for origin in ("https://attacker.example", "null", "http://127.0.0.2:8765"):
        r = client.post("/auth/register", json=body, headers={"Origin": origin})
        assert r.status_code == 403 and r.json()["detail"] == "Cross-site request refused", origin
    assert client.get("/setup/check", headers={"Origin": "https://attacker.example"}).status_code == 200  # reads stay
    assert client.post("/auth/register", json=body, headers={"Origin": "http://testserver"}).status_code == 200
    r = client.post("/auth/login", data={"username": "admin", "password": "secret12"},
                    headers={"Origin": "https://attacker.example"})
    assert r.status_code == 403                                                # no login CSRF either
    assert client.post("/auth/login", data={"username": "admin", "password": "secret12"}).status_code == 200  # CLI/curl
    from cloudify.server import app as app_mod
    try:
        app_mod.configure(allowed_hosts=["dash.example.com"], check_host=False)  # published through a proxy/tunnel
        assert app_mod.origin_allowed("https://dash.example.com", "127.0.0.1:8765")
        assert not app_mod.origin_allowed("https://evil.example.com", "127.0.0.1:8765")
    finally:
        app_mod.configure(check_host=False)


# ------------------------------------------------------------------ security audit, wave 3: one tunnel, one account

def _two_accounts_with_web(env):
    env.login("example.com", "acct-a")
    assert env.cli("account", "connect", "other.org")[0] == 0
    b_cert = glob.glob(str(env.cf / "cloudify/certs/*.pem"))[0]
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart", "--account", "acct-a")
    assert rc == 0, out
    for host in ("app.example.com", "store.example.com"):
        rc, out = env.cli("route", "add", host, "3000", "-t", "web", stdin="n\n")
        assert rc == 0, out
    return b_cert


def _hosts(env, name):
    return [r.get("hostname") for r in yaml.safe_load((env.cf / f"config-{name}.yml").read_text())["ingress"]]


def test_split_stays_in_the_tunnels_account_and_never_drops_a_hostname(systemd_env, monkeypatch):
    env = systemd_env
    b_cert = _two_accounts_with_web(env)
    create_remote(env, "web-ha", b_cert)  # the predictable split name, taken in the other account
    rc, out = env.cli("replica", "split", "web", "store.example.com", "--name", "web-ha")
    assert rc != 0 and "already exists in a connected Cloudflare account" in out, out
    assert "store.example.com" in _hosts(env, "web") and "web-ha" not in state(env)["tunnels"]

    # a hostname whose DNS record can't be pointed at the new tunnel stays on the old one
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "CLOUDIFY_CF_API", "FAKE_CF_ACCOUNTS"):
        monkeypatch.setenv(key, env.vars[key])
    from cloudify.core import replica
    from cloudify.core.manager import TunnelManager
    m = TunnelManager(interactive=False, out=lambda s: None)
    monkeypatch.setattr(m, "_route_dns", lambda host, name, cert, overwrite=False: host != "store.example.com")
    assert not replica.split(m, "web", ["app.example.com", "store.example.com"], "web-two")
    assert "store.example.com" in _hosts(env, "web") and "app.example.com" not in _hosts(env, "web")
    assert "app.example.com" in _hosts(env, "web-two") and "store.example.com" not in _hosts(env, "web-two")
    new = state(env)["tunnels"]["web-two"]
    assert new["account_id"] == "acct-a" and (env.cf / f"{new['tunnel_id']}.json").exists()


def test_delete_never_removes_a_newer_tunnel_with_the_same_name(systemd_env):
    env = systemd_env
    env.login("example.com", "acct-a")
    assert env.cli("tunnel", "create", "web", "--no-autostart")[0] == 0
    old_id = state(env)["tunnels"]["web"]["tunnel_id"]
    # deleted in the Cloudflare dashboard, then a new tunnel got the same name
    db_path = Path(env.vars["FAKE_CF_DB"])
    db = json.loads(db_path.read_text())
    db["tunnels"] = [t for t in db["tunnels"] if t["id"] != old_id]
    db_path.write_text(json.dumps(db))
    create_remote(env, "web")
    new_id = env.remote_tunnel("web")["id"]
    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc == 0 and "no longer exists in Cloudflare" in out, out
    assert env.remote_tunnel("web")["id"] == new_id and "web" not in state(env)["tunnels"]


def test_older_entries_are_matched_by_the_tunnel_id_in_their_config(systemd_env, monkeypatch):
    env = systemd_env
    b_cert = _two_accounts_with_web(env)
    a_id = state(env)["tunnels"]["web"]["tunnel_id"]
    create_remote(env, "web", b_cert)  # same name in the other account
    st = state(env)
    for key in ("tunnel_id", "account_id"):
        st["tunnels"]["web"].pop(key, None)  # as written by an older version; config-web.yml still says tunnel: <id>
    (env.cf / "tunnel_manager_state.json").write_text(json.dumps(st))
    rc, out = env.cli("tunnel", "list")
    assert rc == 0, out
    entry = state(env)["tunnels"]["web"]
    assert entry["account_id"] == "acct-a" and entry["tunnel_id"] == a_id

    # while one account can't be listed, an unrecorded name is not matched to whatever is left
    st = state(env)
    for key in ("tunnel_id", "account_id"):
        st["tunnels"]["web"].pop(key, None)
    (env.cf / "tunnel_manager_state.json").write_text(json.dumps(st))
    (env.cf / "config-web.yml").write_text(yaml.safe_dump({"ingress": [{"service": "http_status:404"}]}))
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "CLOUDIFY_CF_API", "FAKE_CF_ACCOUNTS"):
        monkeypatch.setenv(key, env.vars[key])
    from cloudify.core import cloudflared
    from cloudify.core.manager import TunnelManager
    real = cloudflared.list_tunnels_checked
    monkeypatch.setattr(cloudflared, "list_tunnels_checked",
                        lambda cert=None: None if cert and "certs" not in str(cert) else real(cert))  # acct-a fails
    m = TunnelManager(interactive=False, out=lambda s: None)
    assert m.account_of("web") is None and "account_id" not in m.state.get("web")
    assert not m.delete_tunnel("web", force=True)
    assert {t["account"] for t in env.account()["tunnels"] if t["name"] == "web"} == {"acct-a", "acct-b"}


# ------------------------------------------------------------------ security audit, wave 4

def test_request_bodies_are_capped_before_parsing(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    from cloudify.server import app as app_mod
    big = b'{"username": "admin", "password": "' + b"x" * (app_mod.MAX_BODY + 10) + b'"}'
    r = client.post("/auth/register", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    # without Content-Length (chunked): still refused once the limit is passed
    r = client.post("/auth/register", content=(big[i:i + 65536] for i in range(0, len(big), 65536)),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    # a route that needs a login refuses even earlier, without reading the body at all
    r = client.post("/replicas/import", content=(big[i:i + 65536] for i in range(0, len(big), 65536)),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    body = {"username": "admin", "password": "secret12", "setup_code": SETUP_CODE}
    assert client.post("/auth/register", json=body).status_code == 200   # normal requests are unaffected


def test_quick_tunnels_never_read_the_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core import quick_tunnel
    seen = {}

    class Done(Exception):
        pass

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        raise Done

    monkeypatch.setattr(quick_tunnel.subprocess, "Popen", fake_popen)
    with pytest.raises(Done):
        quick_tunnel.start("localhost", 3000)
    cmd = seen["cmd"]
    config = cmd[cmd.index("--config") + 1]
    assert config.startswith(str(tmp_path / ".cloudflared")) and os.stat(config).st_mode & 0o777 == 0o600
    assert yaml.safe_load(open(config)) == {"no-autoupdate": True}       # no tunnel, credentials or ingress
    assert cmd[cmd.index("--url") + 1] == "http://localhost:3000"


def test_temp_tunnel_stops_when_the_cli_is_terminated(env):
    proc = subprocess.Popen([sys.executable, "-m", "cloudify", "-p", "3000"], env=env.vars, cwd=str(env.home),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = ""
        while "Temporary tunnel active" not in line:
            line = proc.stdout.readline()
            assert line, "cloudify -p exited early"
        assert wait_for(lambda: bool(state(env)["tunnels"]) and any(i.get("temp_tunnel") for i in state(env)["tunnels"].values()))
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not any(i.get("temp_tunnel") for i in state(env)["tunnels"].values())  # stopped and forgotten


def test_bodies_of_requests_without_a_login_are_not_parsed(systemd_env, monkeypatch):
    client, auth = _api(systemd_env, monkeypatch)
    junk = b"[" + b"{}," * 1000 + b"{}"  # not even valid JSON: a parser would answer 422
    for path in ("/tunnel/create", "/replicas/import", "/accounts/token", "/shutdown"):
        r = client.post(path, content=junk, headers={"Content-Type": "application/json"})
        assert r.status_code == 401, (path, r.status_code)
        r = client.post(path, content=junk, headers={"Content-Type": "application/json",
                                                     "Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401, (path, r.status_code)
    # the two public ones still answer normally
    assert client.post("/auth/login", data={"username": "x", "password": "y"}).status_code == 401
    assert client.post("/auth/register", content=junk, headers={"Content-Type": "application/json"}).status_code == 422


def test_quick_tunnel_url_is_never_cloudflares_api_host():
    from cloudify.core.quick_tunnel import TRYCLOUDFLARE_RE
    assert not TRYCLOUDFLARE_RE.search('ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel"')
    assert TRYCLOUDFLARE_RE.search("|  https://a-b-c-d.trycloudflare.com  |").group(0) == "https://a-b-c-d.trycloudflare.com"


def test_temp_tunnel_cli_stops_its_own_process_without_a_record(env, monkeypatch):
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "CLOUDIFY_CF_API"):
        monkeypatch.setenv(key, env.vars[key])
    from cloudify.core.manager import TunnelManager
    m = TunnelManager(interactive=False, out=lambda s: None)
    url = m.create_temp_tunnel(3000)
    assert url
    process = next(iter(m._temp_processes.values()))
    for name in [n for n, i in m.state.tunnels().items() if i.get("temp_tunnel")]:
        m.state.unregister(name)  # e.g. removed by `cloudify temp stop` in another terminal
    assert not m.stop_temp_tunnel(url)
    m.stop_own_temp_tunnels()
    assert process.poll() is not None


# ------------------------------------------------------------------ deferred audit units (parent hardening)

def test_an_abandoned_cloudflare_login_times_out(systemd_env, monkeypatch):
    env = systemd_env
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "CLOUDIFY_CF_API", "FAKE_CF_ACCOUNTS"):
        monkeypatch.setenv(key, env.vars[key])
    monkeypatch.setenv("FAKE_CF_LOGIN_DELAY", "30")   # nobody clicks the link
    from cloudify.core import login, paths
    stale = os.path.join(paths.data_dir(), "cloudify-login-old")
    os.makedirs(stale)
    os.utime(stale, (time.time() - 3 * login.LOGIN_TIMEOUT,) * 2)
    monkeypatch.setattr(login, "LOGIN_TIMEOUT", 2)
    started = time.time()
    assert login.run_login("example.com", echo=False) == login.TIMED_OUT
    assert time.time() - started < 20
    status = login.get_status()
    assert status["status"] == "error" and "wasn't completed" in status["message"]
    assert not os.path.exists(stale)                                          # leftovers are removed
    assert not glob.glob(os.path.join(paths.data_dir(), "cloudify-login-*"))  # and its own one too


def test_an_unreadable_settings_file_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core import jsonfile
    from cloudify.core.state import StateStore
    store = StateStore()
    store.register("web", make_current=False)
    before = open(store.path).read()
    real_open = open

    def flaky_open(path, *args, **kwargs):  # e.g. EMFILE while someone holds every descriptor
        if str(path) == store.path and not args:
            raise OSError(24, "Too many open files")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", flaky_open)
    with pytest.raises(jsonfile.JsonFileError):
        store.register("other", make_current=False)
    monkeypatch.setattr("builtins.open", real_open)
    assert open(store.path).read() == before and "web" in StateStore().tunnels()
    assert jsonfile.read(str(tmp_path / "missing.json"), {"x": 1}) == {"x": 1}  # absent is still just empty


def test_old_status_file_with_an_api_token_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cloudify.core import login
    path = login.status_file()
    with open(path, "w") as f:  # as written by an older version
        json.dump({"status": "completed", "connection": {"id": "c1", "domain": "example.com",
                                                         "api_token": "secret-dns-token", "cert": "/x.pem"}}, f)
    os.chmod(path, 0o644)
    data = login.get_status()
    assert data["connection"] == {"id": "c1", "domain": "example.com"}
    assert "secret-dns-token" not in open(path).read() and os.stat(path).st_mode & 0o777 == 0o600


def test_metrics_listener_on_any_address_counts():
    """A listener on 0.0.0.0 or [::] answers 127.0.0.1 too and blocks cloudflared's bind."""
    import socket as _socket
    from cloudify.core import cloudflared
    for family, address in ((_socket.AF_INET, "0.0.0.0"), (_socket.AF_INET6, "::")):
        try:
            s = _socket.socket(family)
            s.bind((address, 0))
        except OSError:
            continue  # no IPv6 on this machine
        s.listen(1)
        try:
            port = s.getsockname()[1]
            assert cloudflared.listener_uids(port) == {os.getuid()}
            assert cloudflared.port_is_ours(port)
        finally:
            s.close()


# ------------------------------------------------------------------ final test: route changes and stopped tunnels

def test_route_changes_never_start_a_stopped_tunnel(env):
    setup_web(env)  # adding a route starts the tunnel (documented)
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)
    assert env.cli("route", "add", "api.example.com", "3001", "-t", "web", stdin="n\n")[0] == 0
    assert env.cli("tunnel", "stop", "web")[0] == 0
    assert wait_for(lambda: not tunnel_pids("web", env.home))

    rc, out = env.cli("route", "edit", "app.example.com", "--port", "3002", "-t", "web")
    assert rc == 0 and "stopped" in out, out
    rc, out = env.cli("route", "remove", "api.example.com", "-t", "web")
    assert rc == 0 and "stopped" in out, out
    time.sleep(1)
    assert not tunnel_pids("web", env.home)  # the owner stopped it: it stays offline
    ingress = yaml.safe_load((env.cf / "config-web.yml").read_text())["ingress"]
    assert any(r.get("hostname") == "app.example.com" and r.get("service") == "http://localhost:3002" for r in ingress)
    assert all(r.get("hostname") != "api.example.com" for r in ingress)

    assert env.cli("tunnel", "start", "web")[0] == 0  # the changes apply at the next start
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)
    rc, out = env.cli("route", "edit", "app.example.com", "--port", "3003", "-t", "web")
    assert rc == 0 and "restarted" in out.lower(), out  # a running tunnel is still restarted
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)


# ------------------------------------------------------------------ tunnels of connected domains could never start

def test_tunnels_of_connected_domains_find_their_credentials(env):
    """cloudflared writes a new tunnel's credentials next to the certificate it was given: for a domain
    connected through Cloudify that is ~/.cloudflared/cloudify/certs, where `tunnel run` never looks."""
    for domain in ("example.com", "other.org"):
        assert env.cli("account", "connect", domain)[0] == 0
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart", "--account", "other.org")
    assert rc == 0, out
    tunnel_id = env.remote_tunnel("web")["id"]
    certs = env.cf / "cloudify" / "certs"
    assert (env.cf / f"{tunnel_id}.json").exists() and not (certs / f"{tunnel_id}.json").exists()
    rc, out = env.cli("route", "add", "app.other.org", "3000", "-t", "web", stdin="n\n")
    assert rc == 0, out
    assert f"credentials-file: {env.cf}/{tunnel_id}.json" in (env.cf / "config-web.yml").read_text()
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)

    # a tunnel left like this by an earlier version (credentials in certs/, no credentials-file) starts again
    assert env.cli("tunnel", "stop", "web")[0] == 0
    assert wait_for(lambda: not tunnel_pids("web", env.home))
    os.rename(env.cf / f"{tunnel_id}.json", certs / f"{tunnel_id}.json")
    config = env.cf / "config-web.yml"
    config.write_text("".join(l for l in config.read_text().splitlines(True) if not l.startswith("credentials-file:")))
    rc, out = env.cli("tunnel", "start", "web")
    assert rc == 0, out
    assert wait_for(lambda: len(tunnel_pids("web", env.home)) == 1)
    assert (env.cf / f"{tunnel_id}.json").exists() and not (certs / f"{tunnel_id}.json").exists()

    assert env.cli("tunnel", "stop", "web")[0] == 0
    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc == 0, out
    assert not (env.cf / f"{tunnel_id}.json").exists() and not (certs / f"{tunnel_id}.json").exists()
