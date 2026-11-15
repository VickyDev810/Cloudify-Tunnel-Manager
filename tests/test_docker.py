"""Docker image test (opt-in: CLOUDIFY_DOCKER_TESTS=1 pytest tests/test_docker.py).

Builds the image, mounts the fake cloudflared, then checks setup, a tunnel
with auto-start, `docker restart`, and recreating the container from the same
volume (what an image upgrade does).
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("CLOUDIFY_DOCKER_TESTS"), reason="set CLOUDIFY_DOCKER_TESTS=1")

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fakes" / "cloudflared"
IMAGE, NAME, VOLUME, PORT = "cloudify:pytest", "cloudify-pytest", "cloudify-pytest-data", 18799
BASE = f"http://127.0.0.1:{PORT}"


def docker(*args, check=False):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def call(method, path, body=None, token=None, form=False):
    headers, data = {}, None
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def wait_up():
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/setup/check", timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise AssertionError(docker("logs", NAME).stdout)


def setup_code_from_log():
    """`cloudify serve` prints a one-time setup code while no account exists."""
    for _ in range(40):
        logs = docker("logs", NAME)
        m = re.search(r"Setup code: (\S+)", logs.stdout + logs.stderr)
        if m:
            return m.group(1)
        time.sleep(0.25)
    raise AssertionError("no setup code in the container log:\n" + docker("logs", NAME).stdout)


def start_container():
    docker("rm", "-f", NAME)
    docker("run", "-d", "--name", NAME, "-p", f"{PORT}:8765", "-v", f"{VOLUME}:/root/.cloudflared",
           "-v", f"{FAKE}:/usr/local/bin/cloudflared:ro", IMAGE, check=True)
    wait_up()


def tunnel_running(name="web", expected=True):
    """Poll until the tunnel process is (or is not) running inside the container."""
    for _ in range(20):
        if (f"run {name}" in docker("exec", NAME, "ps", "-ww", "-eo", "args").stdout) == expected:
            return True
        time.sleep(0.5)
    return False


def login():
    status, data = call("POST", "/auth/login", {"username": "admin", "password": "secret12"}, form=True)
    assert status == 200, data
    return data["access_token"]


@pytest.fixture(scope="module", autouse=True)
def image():
    docker("build", "-f", str(ROOT / "docker" / "Dockerfile"), "-t", IMAGE, str(ROOT), check=True)
    docker("volume", "rm", "-f", VOLUME)
    yield
    docker("rm", "-f", NAME)
    docker("volume", "rm", "-f", VOLUME)


def test_image_contents():
    out = docker("run", "--rm", "--entrypoint", "sh", IMAGE, "-c", "cloudflared --version && cloudify --version").stdout
    assert "cloudflared version" in out and "cloudify, version" in out


def test_container_lifecycle():
    start_container()
    assert call("POST", "/auth/register", {"username": "admin", "password": "secret12"})[0] == 403
    code = setup_code_from_log()
    assert call("POST", "/auth/register", {"username": "admin", "password": "secret12", "setup_code": code})[0] == 200
    token = login()
    call("POST", "/initial_setup", {}, token)
    for _ in range(40):
        if call("GET", "/status", token=token)[1].get("status") == "completed":
            break
        time.sleep(0.3)
    assert call("GET", "/system/info", token=token)[1]["autostart_backend"] == "cron"

    assert call("POST", "/tunnel/create", {"tunnel_name": "web", "auto_start_on_create": True,
                                           "confirm_autostart": True}, token)[0] == 200
    assert call("POST", "/tunnel/route/add", {"tunnel_name": "web", "domain": "app.example.com",
                                              "end_user_url": "frontend", "localhost_port": 80}, token)[0] == 200
    assert tunnel_running()

    docker("restart", NAME, check=True)
    wait_up()
    assert tunnel_running(), "auto-start after docker restart"

    start_container()  # recreate from the same volume, like an image upgrade
    token = login()
    tunnels = call("GET", "/tunnels", token=token)[1]["tunnels"]
    assert tunnels[0]["routes"][0]["service"] == "http://frontend:80"
    assert tunnel_running(), "auto-start after recreating the container"

    assert call("POST", "/tunnel/delete", {"tunnel_name": "web", "force": True}, token)[0] == 200
    assert tunnel_running(expected=False)
