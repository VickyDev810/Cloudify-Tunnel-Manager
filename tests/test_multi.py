"""Several domains/accounts, route editing and replicas (CLI + API)."""

import base64
import json
import subprocess

import pytest

from conftest import SETUP_CODE, tunnel_pids, wait_for


def connect(env, *domains):
    for domain in domains:
        rc, out = env.cli("account", "connect", domain)
        assert rc == 0 and f"Connected {domain}" in out, out


def live_connectors(env, tunnel_id):
    return [c for c in env.account()["connectors"] if c["tunnel_id"] == tunnel_id
            and subprocess.run(["kill", "-0", str(c["pid"])], capture_output=True).returncode == 0]


# --------------------------------------------------------------------------- accounts

def test_several_accounts_and_domains(systemd_env):
    env = systemd_env
    connect(env, "example.com", "shop.net", "other.org")
    rc, out = env.cli("account", "list")
    assert out.count("acct-a") == 2 and out.count("acct-b") == 1, out

    rc, out = env.cli("tunnel", "create", "web", "--no-autostart")
    assert rc != 0 and "choose one" in out, out
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart", "--account", "shop.net")
    assert rc == 0 and env.remote_tunnel("web")["account"] == "acct-a", out
    rc, out = env.cli("tunnel", "create", "blog", "--no-autostart", "-a", "acct-b")
    assert rc == 0 and env.remote_tunnel("blog")["account"] == "acct-b", out

    # each hostname's DNS record is made with its own domain's certificate
    for host, tunnel in (("app.example.com", "web"), ("store.shop.net", "web"), ("news.other.org", "blog")):
        rc, out = env.cli("route", "add", host, "3000", "-t", tunnel)
        assert rc == 0, out
        assert env.account()["dns"][host] == env.remote_tunnel(tunnel)["id"]
    assert not any(".example.com." in r or r.endswith(".example.com.example.com") for r in env.account()["dns"])

    # a domain of another account (or not connected at all) is refused
    for host in ("x.other.org", "x.unknown.io"):
        rc, out = env.cli("route", "add", host, "1", "-t", "web")
        assert rc != 0 and "not on a domain connected" in out, out

    rc, out = env.cli("tunnel", "list")
    assert "web" in out and "blog" in out
    rc, out = env.cli("tunnel", "delete", "blog", "--force")  # uses acct-b's certificate
    assert rc == 0 and env.remote_tunnel("blog") is None, out


def test_single_cert_from_earlier_versions_keeps_working(env):
    env.login()  # ~/.cloudflared/cert.pem only, like versions < 2.1
    rc, out = env.cli("account", "list")
    assert "default" in out and "unknown" in out, out
    rc, out = env.cli("tunnel", "create", "web", "--no-autostart")
    assert rc == 0, out
    rc, out = env.cli("route", "add", "app.example.com", "3000", "-t", "web")
    assert rc == 0 and env.account()["dns"]["app.example.com"] == env.remote_tunnel("web")["id"], out
    rc, out = env.cli("account", "domain", "default", "example.com")
    assert rc == 0
    connect(env, "other.org")  # adding a second domain works alongside it
    rc, out = env.cli("account", "list")
    assert "example.com" in out and "other.org" in out


# --------------------------------------------------------------------------- route editing

def test_edit_route(systemd_env):
    env = systemd_env
    connect(env, "example.com")
    env.cli("tunnel", "create", "web", "--no-autostart")
    env.cli("route", "add", "app.example.com", "3000", "-t", "web")
    env.cli("route", "add", "api.example.com", "8000", "-t", "web")

    rc, out = env.cli("route", "edit", "app.example.com", "-t", "web", "--port", "3001", "--service", "my_app")
    assert rc == 0, out
    config = (env.cf / "config-web.yml").read_text()
    assert "http://my_app:3001" in config and config.index("app.example.com") < config.index("api.example.com")

    rc, out = env.cli("route", "edit", "app.example.com", "-t", "web", "--hostname", "www.example.com")
    assert rc == 0 and "Delete the DNS record for app.example.com in the Cloudflare dashboard" in out, out
    assert "www.example.com" in (env.cf / "config-web.yml").read_text()
    assert env.account()["dns"]["www.example.com"] == env.remote_tunnel("web")["id"]
    assert wait_for(lambda: len(tunnel_pids("web")) == 1)

    for args, message in ((["nope.example.com", "--port", "1"], "not found"),
                          (["www.example.com", "--hostname", "api.example.com"], "already routed"),
                          (["www.example.com", "--port", "99999"], "Invalid port"),
                          (["www.example.com", "--hostname", "x.other.org"], "not on a domain connected")):
        rc, out = env.cli("route", "edit", *args, "-t", "web")
        assert rc != 0 and message in out, (args, out)


# --------------------------------------------------------------------------- replicas

def test_replicas_across_machines(env, tmp_path):
    a, b = env, env.machine("machine-b")
    connect(a, "example.com", "shop.net")
    a.cli("tunnel", "create", "web", "--no-autostart")
    a.cli("route", "add", "app.example.com", "3000", "-t", "web")
    a.cli("route", "add", "store.shop.net", "4000", "-t", "web")

    rc, out = a.cli("replica", "plan", "-H", "store.shop.net")
    assert "cloudify replica split web store.shop.net" in out, out
    rc, out = a.cli("replica", "split", "web", "store.shop.net", "--name", "web-ha")
    assert rc == 0, out
    ha_id = a.remote_tunnel("web-ha")["id"]
    assert a.account()["dns"]["store.shop.net"] == ha_id                       # DNS re-pointed
    assert "store.shop.net" not in (a.cf / "config-web.yml").read_text()
    assert wait_for(lambda: tunnel_pids("web-ha", a.home) and tunnel_pids("web", a.home))

    bundle = tmp_path / "web-ha.json"
    rc, out = a.cli("replica", "export", str(bundle), "-t", "web-ha", "--passphrase", "correct horse")
    assert rc == 0, out
    text = bundle.read_text()
    assert "TunnelSecret" not in text and json.loads(text)["tunnels"][0]["hostnames"] == ["store.shop.net"]

    rc, out = b.cli("replica", "import", str(bundle), "--passphrase", "wrong one")
    assert rc != 0 and "Wrong passphrase" in out
    rc, out = b.cli("replica", "import", str(bundle), "--passphrase", "correct horse")
    assert rc == 0 and "Started replica of 'web-ha'" in out, out
    assert wait_for(lambda: tunnel_pids("web-ha", b.home))
    assert wait_for(lambda: len(live_connectors(a, ha_id)) == 2)               # Cloudflare sees 2 machines
    rc, out = b.cli("tunnel", "autostart", "web-ha", "--status")
    assert "Enabled" in out and "Not enabled" not in out and "Enabled: No" not in out, out

    rc, out = b.cli("route", "add", "x.shop.net", "1", "-t", "web-ha")
    assert rc != 0 and "is a replica" in out
    rc, out = a.cli("replica", "import", str(bundle), "--passphrase", "correct horse")
    assert rc != 0 and "already runs 'web-ha' as the primary" in out

    # routes change on the primary -> export again -> import updates the replica
    a.cli("route", "edit", "store.shop.net", "-t", "web-ha", "--port", "4001")
    rc, out = a.cli("replica", "export", str(bundle), "-t", "web-ha", "--passphrase", "correct horse")
    rc, out = b.cli("replica", "import", str(bundle), "--passphrase", "correct horse")
    assert rc == 0 and "Updated replica" in out and "4001" in (b.cf / "config-web-ha.yml").read_text(), out

    rc, out = a.cli("tunnel", "delete", "web-ha", "--force")
    assert rc != 0 and "still running it" in out, out                          # B still serves it
    assert tunnel_pids("web-ha", a.home), "a refused delete must leave this machine serving"
    rc, out = b.cli("tunnel", "delete", "web-ha", "--force")
    assert rc == 0 and a.remote_tunnel("web-ha") is not None, out              # replica removal is local
    assert wait_for(lambda: not tunnel_pids("web-ha", b.home)) and tunnel_pids("web-ha", a.home)
    rc, out = a.cli("tunnel", "delete", "web-ha", "--force")
    assert rc == 0 and a.remote_tunnel("web-ha") is None, out
    b.cleanup()


def test_bundle_tampering_is_detected(systemd_env, tmp_path):
    env = systemd_env
    connect(env, "example.com")
    env.cli("tunnel", "create", "web", "--no-autostart")
    env.cli("route", "add", "app.example.com", "3000", "-t", "web")
    path = tmp_path / "b.json"
    rc, out = env.cli("replica", "export", str(path), "-t", "web", "--passphrase", "short")
    assert rc != 0 and "at least 8" in out
    env.cli("replica", "export", str(path), "-t", "web", "--passphrase", "long enough")
    bundle = json.loads(path.read_text())
    raw = bytearray(base64.b64decode(bundle["ciphertext"]))
    raw[5] ^= 1
    bundle["ciphertext"] = base64.b64encode(bytes(raw)).decode()
    path.write_text(json.dumps(bundle))
    other = env.machine("m2")
    rc, out = other.cli("replica", "import", str(path), "--passphrase", "long enough")
    assert rc != 0 and "modified" in out
    path.write_text('{"hello": 1}')
    rc, out = other.cli("replica", "import", str(path), "--passphrase", "long enough")
    assert rc != 0 and "not a Cloudify replica file" in out


# --------------------------------------------------------------------------- API

@pytest.fixture
def api(systemd_env, monkeypatch):
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "FAKE_CF_LOGIN_DELAY", "FAKE_CF_ACCOUNTS",
                "CLOUDIFY_CF_API"):
        monkeypatch.setenv(key, systemd_env.vars[key])
    monkeypatch.chdir(systemd_env.home)
    from fastapi.testclient import TestClient
    from cloudify.server import auth
    from cloudify.server.app import app
    monkeypatch.setattr(auth, "users", auth.UserStore())
    monkeypatch.setattr(auth, "tokens", auth.TokenStore())
    client = TestClient(app)
    client.post("/auth/register", json={"username": "admin", "password": "secret12", "setup_code": SETUP_CODE})
    token = client.post("/auth/login", data={"username": "admin", "password": "secret12"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.env = systemd_env
    return client


def wait_login(api):
    assert wait_for(lambda: api.get("/status").json()["status"] in ("completed", "error_exit_1"), timeout=15)
    return api.get("/status").json()


def test_api_accounts_routes_and_replicas(api):
    assert api.post("/accounts/connect", json={"domain": "not a domain"}).status_code == 400
    for domain in ("example.com", "other.org"):
        assert api.post("/accounts/connect", json={"domain": domain}).status_code == 200
        status = wait_login(api)
        assert status["status"] == "completed" and status["connection"]["domain"] == domain, status
    assert api.post("/accounts/connect", json={"domain": "example.com"}).status_code == 400  # already connected

    data = api.get("/accounts").json()
    assert {a["account_id"] for a in data["accounts"]} == {"acct-a", "acct-b"}
    assert all("cert" not in c for c in data["connections"])  # file paths aren't exposed

    r = api.post("/tunnel/create", json={"tunnel_name": "web", "confirm_autostart": False})
    assert r.status_code == 400 and "choose one" in r.json()["detail"]
    assert api.post("/tunnel/create", json={"tunnel_name": "web", "confirm_autostart": False,
                                            "account_id": "acct-a"}).status_code == 200
    assert api.post("/tunnel/route/add", json={"tunnel_name": "web", "domain": "app.example.com",
                                               "localhost_port": 3000}).status_code == 200
    r = api.post("/tunnel/route/add", json={"tunnel_name": "web", "domain": "a.other.org", "localhost_port": 1})
    assert r.status_code == 400 and "not on a domain connected" in r.json()["detail"]

    r = api.post("/tunnel/route/edit", json={"tunnel_name": "web", "domain": "app.example.com",
                                             "new_domain": "www.example.com", "localhost_port": 3001})
    assert r.status_code == 200, r.text
    tunnel = api.get("/tunnels").json()["tunnels"][0]
    assert tunnel["routes"] == [{"domain": "www.example.com", "service": "http://localhost:3001"}]
    assert tunnel["account_id"] == "acct-a"

    assert api.post("/replicas/export", json={"tunnels": ["web"], "passphrase": "short"}).status_code == 400
    bundle = api.post("/replicas/export", json={"tunnels": ["web"], "passphrase": "long enough"}).json()
    assert bundle["format"] == "cloudify-replica-bundle"
    assert api.get("/tunnels").json()["tunnels"][0]["exported"]
    plan = api.post("/replicas/plan", json={"hostnames": ["www.example.com", "nope.example.com"]}).json()
    assert plan["whole"] == ["web"] and plan["unknown"] == ["nope.example.com"]
    r = api.post("/replicas/import", json={"bundle": bundle, "passphrase": "long enough"})
    assert r.status_code == 400 and "primary" in r.json()["detail"]

    # removing the last domain of an account that has tunnels needs confirmation
    cid = next(c["id"] for c in api.get("/accounts").json()["connections"] if c["domain"] == "example.com")
    r = api.post("/accounts/remove", json={"id": cid})
    assert r.status_code == 409 and "web" in r.json()["detail"]
    assert api.post("/accounts/remove", json={"id": cid, "force": True}).status_code == 200
    assert [c["domain"] for c in api.get("/accounts").json()["connections"]] == ["other.org"]
    assert not (api.env.cf / "cert.pem").exists()  # its copy at cloudflared's default path is retired too
