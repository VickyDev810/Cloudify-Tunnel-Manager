"""Browser end-to-end test of the web UI (skipped if Playwright isn't installed).

Runs a real `cloudify serve` against the fake cloudflared and clicks through
every screen. Set CLOUDIFY_SCREENSHOTS=<dir> to save screenshots.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import SETUP_CODE, tunnel_pids, wait_for

playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(env):
    env.vars["FAKE_CF_LOGIN_DELAY"] = "2.5"  # long enough to see the authorization link
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "cloudify", "serve", "--serve-port", str(port), "--host", "127.0.0.1"],
                            env=env.vars, cwd=str(env.home),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    assert wait_for(lambda: _up(url), timeout=20), "server did not start"
    return proc, url


@pytest.fixture
def server(systemd_env):
    proc, url = start_server(systemd_env)
    yield url, systemd_env
    proc.terminate()
    proc.wait(timeout=10)


def _up(url):
    try:
        import urllib.request
        urllib.request.urlopen(url + "/setup/check", timeout=1)
        return True
    except Exception:
        return False


@pytest.fixture
def page():
    shots = os.environ.get("CLOUDIFY_SCREENSHOTS")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1360, "height": 860}, device_scale_factor=2 if shots else 1)
        pg = context.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        # JS errors only; the browser also logs expected 400/401 responses as console errors
        pg.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource")
              and pg.errors.append(m.text))
        pg.shot = (lambda name: pg.screenshot(path=str(Path(shots) / f"{name}.png"))) if shots else (lambda name: None)
        yield pg
        browser.close()


def toast(page, text):
    expect(page.locator(".toast", has_text=text).last).to_be_visible(timeout=15000)


def test_full_ui_flow(server, page):
    url, env = server
    page.goto(url)

    # ---- first-run setup wizard
    expect(page.get_by_text("Let's get your tunnel manager ready")).to_be_visible()
    page.shot("01-setup-account")
    page.fill("#scode", "WRONG-CODE")
    page.fill("#su", "admin")
    page.fill("#sp", "secret12")
    page.fill("#sp2", "different")
    page.get_by_role("button", name="Create account").click()
    expect(page.locator("#adminError")).to_have_text("Passwords do not match")
    page.fill("#sp2", "secret12")
    page.get_by_role("button", name="Create account").click()
    expect(page.locator("#adminError")).to_contain_text("setup code")   # printed by `cloudify serve`
    page.fill("#scode", SETUP_CODE.lower().replace("-", " "))           # case and separators don't matter
    page.get_by_role("button", name="Create account").click()

    expect(page.get_by_role("button", name="Connect Cloudflare")).to_be_visible()
    page.shot("02-setup-cloudflare")
    page.get_by_role("button", name="Connect Cloudflare").click()
    expect(page.locator(".auth-link")).to_contain_text("dash.cloudflare.com/argotunnel")  # link from cloudflared
    expect(page.get_by_text("You're all set")).to_be_visible(timeout=15000)
    assert (env.cf / "cert.pem").exists()
    page.get_by_role("button", name="Open dashboard").click()

    # ---- tunnels: empty state -> create
    expect(page.get_by_role("heading", name="Tunnels", exact=True)).to_be_visible()
    expect(page.get_by_text("No tunnels yet")).to_be_visible()
    page.locator("[data-create]").first.click()
    page.fill("#tn", "web")
    page.get_by_role("button", name="Create tunnel").click()
    toast(page, 'Tunnel "web" created')
    card = page.locator('[data-tunnel="web"]')
    expect(card).to_be_visible()
    expect(card.locator("[data-autostart]")).to_be_checked()

    # ---- add route: server-side validation error is shown in the dialog
    card.get_by_role("button", name="Add route").click()
    page.fill("#rd", "nodot")
    page.fill("#rp", "3000")
    page.get_by_role("button", name="Add route").last.click()
    expect(page.locator("dialog [data-error]")).to_contain_text("Invalid domain")
    page.fill("#rd", "app.example.com")
    page.get_by_role("button", name="Add route").last.click()
    toast(page, "Route added: app.example.com")
    expect(card.locator(".route-row")).to_contain_text("http://localhost:3000")
    expect(card.locator(".pill-running")).to_be_visible(timeout=15000)
    assert wait_for(lambda: len(tunnel_pids("web")) == 1)
    page.shot("03-tunnels")

    # ---- stop / start
    card.get_by_role("button", name="Stop").click()
    toast(page, 'Tunnel "web" stopped')
    expect(card.locator(".pill-stopped")).to_be_visible()
    assert wait_for(lambda: not tunnel_pids("web"))
    card.get_by_role("button", name="Start").click()
    toast(page, 'Tunnel "web" started')
    expect(card.locator(".pill-running")).to_be_visible()

    # ---- auto-start toggle
    card.locator(".switch").click()
    toast(page, 'Auto-start disabled for "web"')
    expect(page.locator('[data-tunnel="web"] [data-autostart]')).not_to_be_checked()
    page.locator('[data-tunnel="web"] .switch').click()
    toast(page, '"web" will start on boot')

    # ---- unmanaged tunnel; its name is shown as text, never as HTML
    subprocess.run(["cloudflared", "tunnel", "create", "<b>ext</b>"], env=env.vars, capture_output=True)
    page.locator("[data-refresh]").click()
    expect(page.get_by_text("<b>ext</b>")).to_be_visible()

    # ---- overview
    page.click("[data-nav=overview]")
    stats = page.locator(".stat .value")
    expect(stats.nth(0)).to_have_text("1 / 1")   # tunnels online
    expect(stats.nth(1)).to_have_text("1")       # hostnames
    expect(stats.nth(2)).to_have_text("1")       # domains
    # network map: Cloudflare -> hostname -> tunnel -> service, with live links
    topology = page.locator(".topology")
    for text in ("Cloudflare", "app.example.com", "web", "localhost:3000"):
        expect(topology).to_contain_text(text)
    expect(topology.locator("path.live")).to_have_count(3)
    expect(page.locator(".kv").first).to_contain_text("Connected")
    page.shot("04-overview")

    # ---- routes page: filter + remove
    page.click("[data-nav=routes]")
    expect(page.locator("table")).to_contain_text("app.example.com")
    page.shot("05-routes")
    page.fill("#routeSearch", "zzz")
    expect(page.get_by_text('No routes match "zzz".')).to_be_visible()
    page.fill("#routeSearch", "app")
    page.get_by_role("button", name="Remove").click()
    page.get_by_role("button", name="Remove route").click()
    toast(page, "Route app.example.com removed")
    expect(page.get_by_text("No routes yet")).to_be_visible()

    # ---- settings: users + password
    page.click("[data-nav=settings]")
    expect(page.get_by_role("heading", name="Settings")).to_be_visible()
    page.get_by_role("button", name="Add user").click()
    page.fill("#nu", "bob")
    page.fill("#np", "bobpass1")
    page.get_by_role("button", name="Add user").last.click()
    toast(page, 'User "bob" added')
    expect(page.locator("#userList")).to_contain_text("bob")
    page.shot("06-settings")
    page.locator('[data-delete-user="bob"]').click()
    page.get_by_role("button", name="Remove user").click()
    toast(page, "User removed")
    expect(page.locator("#userList")).not_to_contain_text("bob")

    page.get_by_role("button", name="Change password").click()
    page.fill("#cp0", "secret12")
    page.fill("#cp1", "newpass1")
    page.fill("#cp2", "newpass1")
    page.get_by_role("button", name="Change password").last.click()
    toast(page, "Password changed")

    # ---- theme toggle
    before = page.evaluate("document.documentElement.dataset.theme")
    page.click("#themeBtn")
    assert page.evaluate("document.documentElement.dataset.theme") != before
    page.wait_for_timeout(400)  # let colour transitions finish
    page.shot("07-dark")
    page.click("#themeBtn")

    # ---- sign out / sign in again (session survives reload)
    page.click("#logoutBtn")
    expect(page.get_by_text("Sign in to manage your tunnels")).to_be_visible()
    page.fill("#username", "admin")
    page.fill("#password", "secret12")
    page.click("button[type=submit]")
    expect(page.locator("#loginError")).to_contain_text("Invalid username or password")
    page.shot("08-login")
    page.fill("#password", "newpass1")
    page.click("button[type=submit]")
    expect(page.locator(".sidebar")).to_be_visible()
    page.reload()
    expect(page.locator(".sidebar")).to_be_visible()

    # ---- delete tunnel
    page.click("[data-nav=tunnels]")
    page.locator('[data-tunnel="web"] [data-action=delete]').click()
    page.locator("dialog").get_by_role("button", name="Delete tunnel").click()
    toast(page, 'Tunnel "web" deleted')
    expect(page.get_by_text("No tunnels yet")).to_be_visible()
    assert wait_for(lambda: not tunnel_pids("web"))
    assert env.remote_tunnel("web") is None

    assert page.errors == [], page.errors


def test_mobile_layout(server, page):
    url, env = server
    env.login()
    subprocess.run([sys.executable, "-c",
                    "import json,sys,bcrypt; json.dump({'admin': {'username':'admin','is_admin':True,"
                    "'hashed_password': bcrypt.hashpw(b'secret12', bcrypt.gensalt()).decode()}}, open(sys.argv[1],'w'))",
                    str(env.cf / "cloudify" / "users.json")], check=True, env=env.vars)
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(url)
    page.fill("#username", "admin")
    page.fill("#password", "secret12")
    page.click("button[type=submit]")
    expect(page.get_by_role("heading", name="Overview", exact=True)).to_be_visible()
    expect(page.locator(".sidebar")).not_to_be_in_viewport()
    page.locator("[data-menu]").click()
    expect(page.locator(".sidebar")).to_be_in_viewport()
    page.click("[data-nav=tunnels]")
    expect(page.get_by_role("heading", name="Tunnels", exact=True)).to_be_visible()
    expect(page.locator(".sidebar")).not_to_be_in_viewport()
    page.shot("09-mobile")
    assert page.errors == [], page.errors
