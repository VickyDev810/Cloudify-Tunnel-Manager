import re
import subprocess
import sys

import pytest

from cloudify.core import processes
from cloudify.core.validation import (
    is_safe_tunnel_name, is_valid_domain, is_valid_service_host, is_valid_tunnel_name, parse_port, strip_scheme,
)


@pytest.mark.parametrize("name,ok", [("web", True), ("my-app_1.prod", True), ("bad;name", False),
                                     ("has space", False), ("-dash", False), ("x" * 64, False), ("", False)])
def test_new_tunnel_names(name, ok):
    assert is_valid_tunnel_name(name) is ok


@pytest.mark.parametrize("name,ok", [("Old Tunnel", True), ("web", True), ("../etc", False),
                                     ("a/b", False), ("x;rm", False), ("$(id)", False), ("..", False)])
def test_existing_tunnel_names(name, ok):
    assert is_safe_tunnel_name(name) is ok


@pytest.mark.parametrize("domain,ok", [("app.example.com", True), ("*.example.com", True), ("a.b.c.example.co.uk", True),
                                       ("example", False), ("bad domain.com", False), ("-x.example.com", False)])
def test_domains(domain, ok):
    assert is_valid_domain(domain) is ok


@pytest.mark.parametrize("host,ok", [("localhost", True), ("192.168.1.5", True), ("my_app_1", True),
                                     ("host.docker.internal", True), ("[::1]", True), ("bad host", False), ("a:b", False)])
def test_service_hosts(host, ok):
    assert is_valid_service_host(host) is ok


def test_helpers():
    assert strip_scheme("https://App.example.com/") == "App.example.com"
    assert parse_port("3000") == 3000 and parse_port(0) is None and parse_port("x") is None and parse_port(70000) is None


def test_process_pattern_is_exact():
    """`app` must never match a process running tunnel `app2` (or `my.app` match `myxapp`)."""
    procs = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                               "cloudflared", "tunnel", "--config", "/x", "run", name]) for name in ("app2", "my.app")]
    try:
        assert not processes.tunnel_running("app", "/x")
        assert processes.tunnel_running("app2", "/x")
        assert not processes.tunnel_running("myxapp", "/x")
        assert processes.tunnel_running("my.app", "/x")
        assert not processes.tunnel_running("app2", "/other/config.yml")  # another user's tunnel
        processes.kill_tunnel("app2", config_file="/other/config.yml")
        assert processes.tunnel_running("app2", "/x")
        processes.kill_tunnel("app2", config_file="/x")
        procs[0].wait(timeout=5)
        assert not processes.tunnel_running("app2", "/x") and processes.tunnel_running("my.app", "/x")
    finally:
        for p in procs:
            p.kill()
