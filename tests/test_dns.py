"""Automatic DNS record cleanup via the Cloudflare API (against a fake API)."""

import json

import pytest

from conftest import SETUP_CODE, wait_for


def connect(env, domain):
    rc, out = env.cli("account", "connect", domain)
    assert rc == 0, out


def connection_id(env, domain):
    return next(c["id"] for c in json.loads((env.cf / "cloudify" / "accounts.json").read_text())["connections"]
                if c["domain"] == domain)


def dns(env):
    return env.account()["dns"]


def test_dns_records_are_deleted_with_a_token(systemd_env, cf_api):
    env = systemd_env
    connect(env, "example.com")
    env.cli("tunnel", "create", "web", "--no-autostart")
    for host in ("app.example.com", "api.example.com", "x.example.com", "y.example.com"):
        rc, out = env.cli("route", "add", host, "3000", "-t", "web")
        assert rc == 0, out
    tunnel_id = env.remote_tunnel("web")["id"]

    # Without an API token DNS cleanup is off: nothing is deleted, the API isn't even asked
    rc, out = env.cli("route", "remove", "app.example.com", "-t", "web")
    assert rc == 0 and "Delete the DNS record for app.example.com in the Cloudflare dashboard" in out, out
    assert "app.example.com" in dns(env)
    assert not cf_api.calls

    cid = connection_id(env, "example.com")
    rc, out = env.cli("account", "token", cid, stdin="not-a-valid-token\n")
    assert rc != 0 and "isn't allowed" in out, out
    rc, out = env.cli("account", "token", cid, stdin="dns-edit-token\n")
    assert rc == 0 and "DNS cleanup enabled" in out, out
    assert "api_token" not in env.cli("account", "list")[1]

    rc, out = env.cli("route", "remove", "api.example.com", "-t", "web")
    assert rc == 0 and "Deleted the DNS record for api.example.com" in out, out
    assert "api.example.com" not in dns(env)

    # A record that was pointed somewhere else is never deleted
    with cf_api.locked() as db:
        db["dns"]["x.example.com"] = "elsewhere.example.net"
    rc, out = env.cli("route", "remove", "x.example.com", "-t", "web")
    assert "points somewhere else" in out and dns(env)["x.example.com"] == "elsewhere.example.net", out

    # Renaming a route deletes the old record, creates the new one
    rc, out = env.cli("route", "edit", "y.example.com", "-t", "web", "--hostname", "z.example.com")
    assert rc == 0 and "y.example.com" not in dns(env) and dns(env)["z.example.com"] == tunnel_id, out

    # Deleting the tunnel deletes the records of its remaining routes
    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc == 0 and "Deleted the DNS record for z.example.com" in out, out
    assert "z.example.com" not in dns(env)
    assert dns(env)["app.example.com"] == tunnel_id  # the earlier manual one is untouched


def test_removing_a_replica_never_touches_dns(env, tmp_path):
    import sys
    sys.path.insert(0, str(__import__("conftest").FAKES))
    from cloudflare_api import FakeCloudflareApi
    api = FakeCloudflareApi(env.vars["FAKE_CF_DB"])
    try:
        b = env.machine("machine-b")
        for e in (env, b):
            e.vars["CLOUDIFY_CF_API"] = api.url
        connect(env, "example.com")
        env.cli("account", "token", connection_id(env, "example.com"), stdin="dns-edit-token\n")
        env.cli("tunnel", "create", "web", "--no-autostart")
        env.cli("route", "add", "app.example.com", "3000", "-t", "web")
        bundle = tmp_path / "web.json"
        env.cli("replica", "export", str(bundle), "-t", "web", "--passphrase", "long enough")
        rc, out = b.cli("replica", "import", str(bundle), "--passphrase", "long enough")
        assert rc == 0, out
        rc, out = b.cli("tunnel", "delete", "web", "--force")
        assert rc == 0 and "app.example.com" in dns(env), out
        assert not [c for c in api.calls if c[0] == "DELETE"]
        b.cleanup()
    finally:
        api.close()


@pytest.fixture
def api_client(systemd_env, cf_api, monkeypatch):
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "FAKE_CF_LOGIN_DELAY",
                "FAKE_CF_ACCOUNTS", "CLOUDIFY_CF_API"):
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


def test_api_token_and_dns_notes(api_client):
    c = api_client
    c.post("/accounts/connect", json={"domain": "example.com"})
    assert wait_for(lambda: c.get("/status").json()["status"] == "completed", timeout=15)
    cid = c.get("/accounts").json()["connections"][0]["id"]
    assert c.get("/accounts").json()["connections"][0]["dns_cleanup"] is False

    r = c.post("/accounts/token", json={"id": cid, "token": "wrong"})
    assert r.status_code == 400 and "isn't allowed" in r.json()["detail"]
    assert c.post("/accounts/token", json={"id": cid, "token": "dns-edit-token"}).status_code == 200
    connection = c.get("/accounts").json()["connections"][0]
    assert connection["dns_cleanup"] is True and "api_token" not in connection

    c.post("/tunnel/create", json={"tunnel_name": "web", "confirm_autostart": False})
    c.post("/tunnel/route/add", json={"tunnel_name": "web", "domain": "app.example.com", "localhost_port": 3000})
    r = c.post("/tunnel/route/remove", json={"tunnel_name": "web", "domain": "app.example.com"})
    assert r.json()["dns"] == [{"hostname": "app.example.com", "outcome": "deleted", "detail": None}]

    assert c.post("/accounts/token", json={"id": cid, "token": ""}).status_code == 200
    assert c.get("/accounts").json()["connections"][0]["dns_cleanup"] is False


def test_check_and_repair_dns(systemd_env, cf_api):
    """A record deleted by accident in the Cloudflare dashboard is detected and recreated."""
    env = systemd_env
    connect(env, "example.com")
    env.cli("account", "token", connection_id(env, "example.com"), stdin="dns-edit-token\n")
    env.cli("tunnel", "create", "web", "--no-autostart")
    env.cli("route", "add", "app.example.com", "3000", "-t", "web")
    env.cli("route", "add", "api.example.com", "8000", "-t", "web")
    tunnel_id = env.remote_tunnel("web")["id"]

    rc, out = env.cli("route", "check", "-t", "web")
    assert rc == 0 and out.count("ok") == 2, out

    with cf_api.locked() as db:            # someone deletes / repoints records in the dashboard
        del db["dns"]["app.example.com"]
        db["dns"]["api.example.com"] = "elsewhere.example.net"
    rc, out = env.cli("route", "check", "-t", "web")
    assert rc != 0 and "MISSING" in out and "points elsewhere" in out and "cloudify route repair" in out, out

    rc, out = env.cli("route", "repair", "app.example.com", "-t", "web")
    assert rc == 0 and dns(env)["app.example.com"] == tunnel_id, out
    rc, out = env.cli("route", "repair", "api.example.com", "-t", "web")
    assert rc != 0 and "re-point it" in out and dns(env)["api.example.com"] == "elsewhere.example.net", out
    rc, out = env.cli("route", "repair", "api.example.com", "-t", "web", "--overwrite")
    assert rc == 0 and dns(env)["api.example.com"] == tunnel_id, out
    rc, out = env.cli("route", "check", "-t", "web")
    assert rc == 0 and out.count("ok") == 2, out
    rc, out = env.cli("route", "repair", "nope.example.com", "-t", "web")
    assert rc != 0 and "not a route" in out


def test_public_lookup_without_token(monkeypatch):
    import socket
    from cloudify.core import dns_api

    def fake_lookup(host, port):
        if host.startswith("gone."):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.16.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_lookup)
    assert dns_api.public_lookup("app.example.com") == "resolves"
    assert dns_api.public_lookup("gone.example.com") == "missing"
    assert dns_api.public_lookup("*.example.com") == "resolves"
