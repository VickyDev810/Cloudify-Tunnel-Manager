"""Web API tests (in-process, using the same requests the web UI sends)."""

import json
import os
import subprocess
import time

import bcrypt
import pytest
from fastapi.testclient import TestClient

from conftest import SETUP_CODE, tunnel_pids, wait_for


@pytest.fixture
def client(systemd_env, monkeypatch):
    for key in ("HOME", "PATH", "USER", "FAKE_CF_DB", "CLOUDIFY_OFFLINE", "FAKE_CF_LOGIN_DELAY", "FAKE_CF_ACCOUNTS",
                "CLOUDIFY_CF_API"):
        monkeypatch.setenv(key, systemd_env.vars[key])
    monkeypatch.chdir(systemd_env.home)
    from cloudify.server import auth
    from cloudify.server.app import app
    monkeypatch.setattr(auth, "users", auth.UserStore())   # fresh users.json under this HOME
    monkeypatch.setattr(auth, "tokens", auth.TokenStore())
    import cloudify.server.routes as routes
    monkeypatch.setattr(routes, "auth", auth)
    client = TestClient(app)
    client.env = systemd_env
    return client


def login(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


def auth_headers(client, username="admin", password="secret12"):
    response = login(client, username, password)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_ui_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "js/app.js" in page.text
    for asset in ("/css/app.css", "/js/app.js", "/js/api.js", "/js/ui.js", "/js/views/tunnels.js"):
        assert client.get(asset).status_code == 200, asset
    assert client.get("/api/docs").status_code == 404  # opt-in with `cloudify serve --api-docs`


def test_first_run_and_accounts(client):
    check = client.get("/setup/check").json()
    assert check["needs_setup"] is True and check["cloudflare_logged_in"] is False
    assert client.post("/auth/register", json={"username": "admin", "password": "123"}).status_code == 400
    assert client.post("/auth/register", json={"username": "admin", "password": "secret12"}).status_code == 403
    r = client.post("/auth/register", json={"username": "admin", "password": "secret12", "setup_code": SETUP_CODE})
    assert r.status_code == 200 and r.json()["is_admin"] is True
    assert client.get("/setup/check").json()["needs_setup"] is False

    # registration is closed once an account exists
    assert client.post("/auth/register", json={"username": "eve", "password": "secret12"}).status_code == 403
    assert login(client, "admin", "wrong").status_code == 401
    assert client.get("/tunnels").status_code == 401

    h = auth_headers(client)
    assert client.get("/auth/me", headers=h).json() == {"username": "admin", "is_admin": True}
    assert client.post("/auth/register", json={"username": "bob", "password": "secret12"}, headers=h).status_code == 200
    assert {u["username"] for u in client.get("/auth/users", headers=h).json()["users"]} == {"admin", "bob"}
    bob = auth_headers(client, "bob", "secret12")

    assert client.post("/auth/users/delete", json={"username": "admin"}, headers=h).status_code == 400  # not yourself
    assert client.post("/auth/users/delete", json={"username": "bob"}, headers=h).status_code == 200
    assert client.get("/auth/me", headers=bob).status_code == 401  # deleted user's session is gone

    r = client.post("/auth/change-password", json={"current_password": "nope", "new_password": "newpass1"}, headers=h)
    assert r.status_code == 400
    r = client.post("/auth/change-password", json={"current_password": "secret12", "new_password": "newpass1"}, headers=h)
    assert r.status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 200      # this session stays signed in
    assert login(client, "admin", "secret12").status_code == 401
    assert login(client, "admin", "newpass1").status_code == 200

    client.post("/auth/logout", headers=h)
    assert client.get("/auth/me", headers=h).status_code == 401


def test_legacy_users_file_is_migrated(client):
    legacy = {"old": {"username": "old", "hashed_password": bcrypt.hashpw(b"oldpass1", bcrypt.gensalt(12)).decode(),
                      "is_admin": True, "created_at": "x"}}
    (client.env.home / "users.json").write_text(json.dumps(legacy))  # where versions < 2 kept it
    from cloudify.server import auth
    auth.users._migrated = False
    assert client.get("/setup/check").json()["user_count"] == 1
    assert login(client, "old", "oldpass1").status_code == 200


def test_cloudflare_login_flow(client):
    client.post("/auth/register", json={"username": "admin", "password": "secret12", "setup_code": SETUP_CODE})
    h = auth_headers(client)
    assert client.post("/initial_setup", headers=h).status_code == 200
    seen = set()
    for _ in range(40):
        status = client.get("/status", headers=h).json()
        seen.add(status["status"])
        if status["status"] == "completed":
            break
        time.sleep(0.2)
    assert status["status"] == "completed" and (client.env.cf / "cert.pem").exists(), seen
    # already logged in -> completed again, not an error
    client.post("/initial_setup", headers=h)
    assert wait_for(lambda: client.get("/status", headers=h).json()["status"] == "completed")


def test_tunnels_and_routes(client):
    client.env.login()
    client.post("/auth/register", json={"username": "admin", "password": "secret12", "setup_code": SETUP_CODE})
    h = auth_headers(client)
    post = lambda path, **body: client.post(path, json=body, headers=h)

    assert post("/tunnel/create", tunnel_name="web", auto_start_on_create=True, confirm_autostart=True).status_code == 200
    assert post("/tunnel/create", tunnel_name="noauto", auto_start_on_create=False, confirm_autostart=False).status_code == 200
    r = post("/tunnel/create", tunnel_name="bad name;x", auto_start_on_create=False, confirm_autostart=False)
    assert r.status_code == 400 and "Invalid tunnel name" in r.json()["detail"]

    r = post("/tunnel/start", tunnel_name="noauto")
    assert r.status_code == 400 and "Add a route first" in r.json()["detail"]  # reason reaches the UI

    assert post("/tunnel/route/add", tunnel_name="web", domain="app.example.com", end_user_url="localhost",
                localhost_port=3000).status_code == 200
    assert post("/tunnel/route/add", tunnel_name="noauto", domain="https://NA.example.com/", end_user_url="http://frontend",
                localhost_port=5000).status_code == 200
    assert "http://frontend:5000" in (client.env.cf / "config-noauto.yml").read_text()
    assert not (client.env.home / ".config/systemd/user/cloudflared-tunnel-noauto.service").exists()
    r = post("/tunnel/route/add", tunnel_name="web", domain="bad domain", end_user_url="localhost", localhost_port=1)
    assert r.status_code == 400 and "Invalid domain" in r.json()["detail"]

    assert wait_for(lambda: tunnel_pids("web") and tunnel_pids("noauto"))
    data = client.get("/tunnels", headers=h).json()
    t = {x["name"]: x for x in data["tunnels"]}
    assert set(t) == {"web", "noauto"} and data["unmanaged"] == []
    assert t["web"]["status"] == "running (service)" and t["noauto"]["status"] == "running (manual)"
    assert t["noauto"]["routes"] == [{"domain": "na.example.com", "service": "http://frontend:5000"}]
    assert client.get("/tunnel/status", params={"tunnel_name": "web"}, headers=h).json()["status"].startswith("running")
    assert len(client.get("/tunnel/routes", params={"tunnel_name": "web"}, headers=h).json()["routes"]) == 1

    assert post("/tunnel/stop", tunnel_name="noauto").status_code == 200
    assert wait_for(lambda: not tunnel_pids("noauto")) and tunnel_pids("web")
    assert post("/tunnel/start", tunnel_name="noauto").status_code == 200
    assert wait_for(lambda: len(tunnel_pids("noauto")) == 1)
    assert post("/tunnel/autostart/setup", tunnel_name="noauto").status_code == 200
    assert wait_for(lambda: len(tunnel_pids("noauto")) == 1)
    assert post("/tunnel/autostart/remove", tunnel_name="noauto").status_code == 200
    assert post("/tunnel/route/remove", tunnel_name="web", domain="app.example.com").status_code == 200
    assert post("/tunnel/route/remove", tunnel_name="web", domain="app.example.com").status_code == 400

    subprocess.run(["cloudflared", "tunnel", "create", "ext"], capture_output=True)
    assert [u["name"] for u in client.get("/tunnels", headers=h).json()["unmanaged"]] == ["ext"]
    assert post("/tunnel/adopt", tunnel_name="ext").status_code == 200

    for name in ("web", "noauto", "ext"):
        assert post("/tunnel/delete", tunnel_name=name, force=False).status_code == 200, name
    assert wait_for(lambda: not tunnel_pids("web") and not tunnel_pids("noauto"))
    assert client.get("/tunnels", headers=h).json()["tunnels"] == []


def test_system_info(client):
    client.post("/auth/register", json={"username": "admin", "password": "secret12", "setup_code": SETUP_CODE})
    h = auth_headers(client)
    info = client.get("/system/info", headers=h).json()
    assert info["cloudflared"]["installed"] and info["cloudflared"]["version"] == "2025.1.0"
    assert info["autostart_backend"] == "systemd"
    r = client.post("/system/cloudflared/update", headers=h)
    assert r.status_code == 400 and "package manager" in r.json()["detail"]
