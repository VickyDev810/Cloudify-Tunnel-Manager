"""Browser test: several domains/accounts, route editing and replicas across two machines."""

import json
import subprocess
import sys

import bcrypt
import pytest

from conftest import tunnel_pids, wait_for
from test_ui import expect, page, start_server, toast  # noqa: F401  (page is a fixture)


def make_admin(env):
    path = env.cf / "cloudify"
    path.mkdir(parents=True, exist_ok=True)
    (path / "users.json").write_text(json.dumps({"admin": {
        "username": "admin", "is_admin": True, "hashed_password": bcrypt.hashpw(b"secret12", bcrypt.gensalt(4)).decode()}}))


def sign_in(page, url):
    page.goto(url)
    expect(page.locator("#username").or_(page.locator(".sidebar"))).to_be_visible()
    if page.locator(".sidebar").is_visible():
        return  # this machine's session is still active
    page.fill("#username", "admin")
    page.fill("#password", "secret12")
    page.click("button[type=submit]")
    expect(page.locator(".sidebar")).to_be_visible()


def connect_domain(page, domain):
    page.locator("[data-connect]").first.click()
    dialog = page.locator("dialog")
    dialog.locator("#cfDomain").fill(domain)
    dialog.get_by_role("button", name="Connect Cloudflare").click()
    expect(dialog.locator(".auth-link")).to_contain_text("argotunnel")
    toast(page, "Cloudflare domain connected")
    expect(page.locator(f'[data-connection] >> text="{domain}"')).to_be_visible()


@pytest.fixture
def machines(systemd_env):
    sys.path.insert(0, str(__import__("conftest").FAKES))
    from cloudflare_api import FakeCloudflareApi
    api = FakeCloudflareApi(systemd_env.vars["FAKE_CF_DB"])
    a, b = systemd_env, systemd_env.machine("machine-b")
    servers = []
    for env in (a, b):
        env.vars["CLOUDIFY_CF_API"] = api.url
        make_admin(env)
        servers.append(start_server(env))
    yield (servers[0][1], a), (servers[1][1], b)
    for proc, _ in servers:
        proc.terminate()
        proc.wait(timeout=10)
    b.cleanup()
    api.close()


def test_domains_routes_and_replicas(machines, page, tmp_path):
    (url_a, env_a), (url_b, env_b) = machines
    sign_in(page, url_a)

    # ---- Domains: three domains in two Cloudflare accounts
    page.click("[data-nav=domains]")
    expect(page.get_by_text("No Cloudflare domain connected")).to_be_visible()
    for domain in ("example.com", "other.org", "shop.net"):
        connect_domain(page, domain)
    sections = page.locator("[data-account]")
    expect(sections).to_have_count(2)
    expect(page.locator('[data-account="acct-a"]')).to_contain_text("example.com")
    expect(page.locator('[data-account="acct-a"]')).to_contain_text("shop.net")
    expect(page.locator('[data-account="acct-b"]')).to_contain_text("other.org")
    page.shot("10-domains")

    # ---- Tunnels in different accounts
    page.click("[data-nav=tunnels]")
    for name, account in (("web", "acct-a"), ("blog", "acct-b")):
        page.locator("[data-create]").first.click()
        page.fill("#tn", name)
        page.select_option("#ta", account)
        page.locator("dialog .switch").click()  # no auto-start needed here
        page.get_by_role("button", name="Create tunnel").click()
        toast(page, f'Tunnel "{name}" created')
    assert env_a.remote_tunnel("web")["account"] == "acct-a" and env_a.remote_tunnel("blog")["account"] == "acct-b"

    # ---- Add routes: the domain picker only offers the tunnel's account
    def add_route(tunnel, sub, zone, port):
        page.locator(f'[data-tunnel="{tunnel}"] [data-action=route]').click()
        options = page.locator("dialog select[name=zone] option").all_inner_texts()
        assert options == (["example.com", "shop.net"] if tunnel == "web" else ["other.org"]), options
        page.fill("#rs", sub)
        page.select_option("dialog select[name=zone]", zone)
        page.fill("#rp", str(port))
        page.get_by_role("button", name="Add route").last.click()
        toast(page, f"Route added: {sub}.{zone}")

    add_route("web", "app", "example.com", 3000)
    add_route("web", "store", "shop.net", 4000)
    add_route("blog", "news", "other.org", 5000)
    dns = env_a.account()["dns"]
    assert dns["app.example.com"] == dns["store.shop.net"] == env_a.remote_tunnel("web")["id"]
    assert dns["news.other.org"] == env_a.remote_tunnel("blog")["id"]  # created with other.org's certificate

    # ---- Edit a route: new subdomain and port
    row = page.locator('[data-tunnel="web"] [data-domain="app.example.com"]')
    row.locator("[data-edit-route]").click()
    expect(page.locator("#rs")).to_have_value("app")
    expect(page.locator("#rp")).to_have_value("3000")
    page.fill("#rs", "www")
    page.fill("#rp", "3001")
    page.get_by_role("button", name="Save changes").click()
    toast(page, "Route moved to www.example.com")
    expect(page.locator('[data-tunnel="web"] [data-domain="www.example.com"]')).to_contain_text("http://localhost:3001")
    assert "http://localhost:3001" in (env_a.cf / "config-web.yml").read_text()
    assert env_a.account()["dns"]["www.example.com"] == env_a.remote_tunnel("web")["id"]

    # ---- DNS cleanup token (step-by-step dialog), then detect + repair a deleted record
    page.click("[data-nav=domains]")
    page.locator('[data-connection]', has_text="example.com").locator("[data-dns-token]").click()
    expect(page.locator("dialog .token-steps li")).to_have_count(7)
    page.shot("17-token-steps")
    page.fill("#apiToken", "wrong-token")
    page.get_by_role("button", name="Save token").click()
    expect(page.locator("dialog [data-error]")).to_contain_text("isn't allowed")
    page.fill("#apiToken", "dns-edit-token")
    page.get_by_role("button", name="Save token").click()
    toast(page, "Automatic DNS cleanup enabled for example.com")
    expect(page.locator('[data-connection]', has_text="example.com").locator("[data-dns-token]")).to_contain_text("DNS cleanup on")

    db_path = env_a.vars["FAKE_CF_DB"]
    db = json.load(open(db_path))
    del db["dns"]["www.example.com"]          # deleted by accident in the Cloudflare dashboard
    json.dump(db, open(db_path, "w"))
    page.click("[data-nav=tunnels]")
    page.locator('[data-tunnel="web"] [data-check-dns]').click()
    toast(page, 'DNS problem on "web"')
    row = page.locator('[data-tunnel="web"] [data-domain="www.example.com"]')
    expect(row.locator("[data-dns-status]")).to_have_text("DNS record missing")
    page.shot("16-dns-missing")
    row.locator("[data-dns-repair]").click()
    toast(page, 'DNS record for www.example.com now points at "web"')
    expect(row.locator("[data-dns-status]")).to_have_text("DNS ok")
    assert env_a.account()["dns"]["www.example.com"] == env_a.remote_tunnel("web")["id"]

    # ---- Routes page grouped by domain
    page.click("[data-nav=routes]")
    for zone in ("example.com", "shop.net", "other.org"):
        expect(page.locator(f'[data-zone="{zone}"]')).to_be_visible()
    page.select_option("#domainFilter", "shop.net")
    expect(page.locator("[data-zone]")).to_have_count(1)
    page.select_option("#domainFilter", "")
    page.shot("11-routes-by-domain")

    # ---- Redundancy: replicate only store.shop.net (needs a split)
    page.click("[data-nav=redundancy]")
    page.locator('[data-host="store.shop.net"]').check()
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_text("shares tunnel")).to_be_visible()
    page.shot("12-redundancy-split")
    page.get_by_role("button", name="Move hostnames & continue").click()
    toast(page, 'Moved store.shop.net to tunnel "web-ha"')
    assert wait_for(lambda: (env_a.remote_tunnel("web-ha") or {}).get("id") == env_a.account()["dns"]["store.shop.net"])
    page.fill("#pp1", "correct horse")
    page.fill("#pp2", "correct horse")
    with page.expect_download() as download:
        page.get_by_role("button", name="Download replica file").click()
    bundle_path = tmp_path / download.value.suggested_filename
    download.value.save_as(bundle_path)
    bundle = json.loads(bundle_path.read_text())
    assert bundle["tunnels"] == [{"name": "web-ha", "hostnames": ["store.shop.net"]}]
    assert "TunnelSecret" not in bundle_path.read_text()  # encrypted
    expect(page.get_by_text("cloudify replica import")).to_be_visible()
    page.shot("13-redundancy-done")

    # ---- Machine B imports it
    sign_in(page, url_b)
    page.click("[data-nav=redundancy]")
    page.locator("[data-tab=import]").click()
    page.set_input_files("#bundleFile", str(bundle_path))
    expect(page.locator("#bundleSummary")).to_contain_text("store.shop.net")
    page.fill("#ipp", "wrong passphrase")
    page.get_by_role("button", name="Import and start").click()
    expect(page.locator("#importError")).to_contain_text("Wrong passphrase")
    page.fill("#ipp", "correct horse")
    page.get_by_role("button", name="Import and start").click()
    toast(page, "This machine now also runs web-ha")
    assert wait_for(lambda: tunnel_pids("web-ha", env_b.home))
    page.click("[data-nav=tunnels]")
    card = page.locator('[data-tunnel="web-ha"]')
    expect(card).to_contain_text("Replica of")
    expect(card.locator("[data-action=route]")).to_have_count(0)  # routes are managed on the primary
    page.shot("14-replica")

    # ---- Machine A sees two machines serving web-ha
    sign_in(page, url_a)
    page.click("[data-nav=redundancy]")
    expect(page.locator("[data-tab=status]")).to_have_class("active")  # status dashboard is the default now
    expect(page.get_by_role("button", name="Refresh")).to_be_visible()   # manual refresh, no polling
    card = page.locator('[data-replica-tunnel="web-ha"]')
    expect(card.locator("[data-connected]")).to_have_text("2 machines connected · redundant", timeout=20000)
    expect(card.locator("tbody tr")).to_have_count(2)
    expect(card.locator(".tag-accent", has_text="this machine")).to_have_count(1)  # identified via cloudflared /ready
    expect(card).to_contain_text("4/4")
    page.shot("15-replica-dashboard")

    # ---- Remove the replica on B; A keeps serving
    sign_in(page, url_b)
    page.click("[data-nav=tunnels]")
    page.locator('[data-tunnel="web-ha"] [data-action=delete]').click()
    page.locator("dialog").get_by_role("button", name="Remove replica").click()
    toast(page, 'Replica of "web-ha" removed')
    assert wait_for(lambda: not tunnel_pids("web-ha", env_b.home)) and tunnel_pids("web-ha", env_a.home)
    assert env_a.remote_tunnel("web-ha") is not None

    assert page.errors == [], page.errors
