"""End-to-end CLI tests (each runs with systemd and with cron auto-start)."""

import json

from conftest import tunnel_pids, wait_for


def setup_tunnel_with_route(env, name="web", domain="app.example.com", port="3000", autostart=True):
    env.login()
    rc, out = env.cli("tunnel", "create", name, stdin="y\n" if autostart else "", *([] if autostart else ["--no-autostart"]))
    assert rc == 0, out
    rc, out = env.cli("route", "add", domain, port, "-t", name, stdin="n\n")
    assert rc == 0, out
    assert wait_for(lambda: len(tunnel_pids(name)) == 1), out


def test_help_and_version(env):
    rc, out = env.cli("--help")
    assert rc == 0 and "tunnel" in out and "serve" in out
    rc, out = env.cli("--version")
    assert rc == 0 and "version" in out


def test_setup_login_flow(env):
    rc, out = env.cli("tunnel", "list")
    assert rc == 0, out  # not logged in: no crash
    rc, out = env.cli("setup", stdin="n\n")
    assert rc == 0 and "Login completed" in out and (env.cf / "cert.pem").exists(), out
    status = json.loads((env.cf / "cloudify" / "status.json").read_text())
    assert status["status"] == "completed" and "argotunnel" in status["url"]
    rc, out = env.cli("setup", stdin="n\n")
    assert rc == 0 and "Already logged in" in out


def test_create_rejects_bad_names(env):
    env.login()
    rc, out = env.cli("tunnel", "create", "bad;name", "--no-autostart")
    assert rc != 0 and "Invalid tunnel name" in out


def test_tunnel_lifecycle(env):
    setup_tunnel_with_route(env)
    if env.mode == "systemd":
        assert (env.home / ".config/systemd/user/cloudflared-tunnel-web.service").exists()
    else:
        assert "start-tunnel-web.sh" in (env.home / ".fakecrontab").read_text()

    tid = env.remote_tunnel("web")["id"]
    config = (env.cf / "config-web.yml").read_text()
    assert f"tunnel: {tid}" in config and f"{tid}.json" in config
    assert "http://localhost:3000" in config and config.strip().endswith("http_status:404")
    assert env.account()["dns"]["app.example.com"] == tid

    rc, out = env.cli("tunnel", "create", "web")
    assert rc == 0 and "already exists" in out

    rc, out = env.cli("route", "add", "api.example.com", "8000", "--service", "my_app_1")
    assert rc == 0 and "http://my_app_1:8000" in (env.cf / "config-web.yml").read_text(), out
    rc, out = env.cli("route", "add", "*.wild.example.com", "9000", "-t", "web")
    assert rc == 0, out
    rc, out = env.cli("route", "list")
    assert out.count("example.com") >= 3, out
    assert wait_for(lambda: len(tunnel_pids("web")) == 1), "config changes must leave exactly one process"

    rc, out = env.cli("tunnel", "status", "web")
    assert rc == 0 and "running" in out
    rc, out = env.cli("tunnel", "list")
    assert "web" in out and "running" in out

    rc, out = env.cli("tunnel", "stop", "web")
    assert rc == 0 and wait_for(lambda: not tunnel_pids("web")), out
    rc, out = env.cli("tunnel", "start", "web")
    assert rc == 0 and wait_for(lambda: len(tunnel_pids("web")) == 1), out
    rc, out = env.cli("tunnel", "start", "web")
    assert rc == 0 and "already running" in out

    rc, out = env.cli("route", "remove", "api.example.com")
    assert rc == 0 and "api.example.com" not in (env.cf / "config-web.yml").read_text()
    rc, out = env.cli("route", "remove", "nope.example.com")
    assert rc != 0 and "not found" in out

    rc, out = env.cli("tunnel", "autostart", "web", "--disable")
    assert rc == 0, out
    rc, out = env.cli("tunnel", "autostart", "web", "--status")
    assert rc == 0 and ("Not enabled" in out or "Enabled: No" in out), out
    rc, out = env.cli("tunnel", "autostart", "web", "--enable")
    assert rc == 0 and wait_for(lambda: len(tunnel_pids("web")) == 1), out

    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc == 0, out
    assert env.remote_tunnel("web") is None
    assert not (env.cf / f"{tid}.json").exists() and not (env.cf / "config-web.yml").exists()
    assert wait_for(lambda: not tunnel_pids("web"))
    assert not (env.home / ".config/systemd/user/cloudflared-tunnel-web.service").exists()
    if env.mode == "cron":
        assert "start-tunnel-web" not in (env.home / ".fakecrontab").read_text()


def test_tunnels_are_isolated(env):
    setup_tunnel_with_route(env, "web")
    setup_tunnel_with_route(env, "web2", "two.example.com", "4000", autostart=False)
    rc, out = env.cli("tunnel", "stop", "web2")
    assert wait_for(lambda: not tunnel_pids("web2")) and len(tunnel_pids("web")) == 1
    rc, out = env.cli("tunnel", "delete", "web2", "--force")
    assert rc == 0 and len(tunnel_pids("web")) == 1, out


def test_route_validation(env):
    setup_tunnel_with_route(env)
    for args in (["notadomain", "80"], ["x.example.com", "70000"], ["x.example.com", "80", "--service", "bad host"]):
        rc, out = env.cli("route", "add", *args, "-t", "web")
        assert rc != 0, (args, out)


def test_adopt_existing_tunnel_with_legacy_name(env):
    env.login()
    import subprocess
    subprocess.run(["cloudflared", "tunnel", "create", "Old Tunnel"], env=env.vars, capture_output=True)
    rc, out = env.cli("tunnel", "adopt", "Old Tunnel")
    assert rc == 0 and "Adopted" in out
    rc, out = env.cli("tunnel", "status", "Old Tunnel")
    assert rc == 0 and "Old Tunnel" in out


def test_misc_commands(env):
    rc, out = env.cli("temp", "list")
    assert rc == 0 and "No temporary tunnels" in out
    rc, out = env.cli("temp", "stop", "https://nope.trycloudflare.com")
    assert rc != 0 and "not found" in out
    rc, out = env.cli("cloudflared", "version")
    assert rc == 0 and "2025.1.0" in out
    rc, out = env.cli("cloudflared", "update")
    assert rc != 0 and "package manager" in out
    rc, out = env.cli("tunnel", "delete", "nonexistent", "--force")
    assert rc != 0


def test_resume_restores_lost_autostart(env):
    """Docker: recreating the container loses crontab/scripts; `tunnel resume` restores them."""
    setup_tunnel_with_route(env)
    env.cli("tunnel", "stop", "web")
    assert wait_for(lambda: not tunnel_pids("web"))
    # simulate a fresh container: auto-start entries gone, state still in the volume
    unit = env.home / ".config/systemd/user/cloudflared-tunnel-web.service"
    if unit.exists():
        unit.unlink()
    (env.home / ".fakecrontab").write_text("")
    (env.home / ".fakesystemd.json").unlink(missing_ok=True)

    rc, out = env.cli("tunnel", "resume")
    assert rc == 0 and "Started 1 auto-start tunnel(s): web" in out, out
    assert wait_for(lambda: len(tunnel_pids("web")) == 1)
    rc, out = env.cli("tunnel", "autostart", "web", "--status")
    assert "Enabled" in out and "Not enabled" not in out and "Enabled: No" not in out, out

    rc, out = env.cli("tunnel", "resume")  # idempotent
    assert rc == 0 and wait_for(lambda: len(tunnel_pids("web")) == 1)


def test_delete_tunnel_already_removed_in_cloudflare(env):
    """A tunnel deleted in the Cloudflare dashboard can still be removed from Cloudify."""
    setup_tunnel_with_route(env)
    import subprocess
    subprocess.run(["cloudflared", "tunnel", "delete", "-f", "web"], env=env.vars, capture_output=True)
    assert env.remote_tunnel("web") is None
    rc, out = env.cli("tunnel", "delete", "web", "--force")
    assert rc == 0 and "no longer exists in Cloudflare" in out, out
    assert wait_for(lambda: not tunnel_pids("web"))
    rc, out = env.cli("tunnel", "list")
    assert "No managed tunnels" in out
