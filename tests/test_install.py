"""install.sh on clean Linux distributions (opt-in: CLOUDIFY_INSTALL_TESTS=1, needs Docker + internet).

Each case starts a fresh container with this checkout mounted read-only at /src, runs install.sh
from scratch (as root, or as a normal user with sudo), checks that `cloudify` and `cloudflared` work
and the dashboard answers, re-runs the installer, then uninstalls.

The update-* cases fake a Cloudify 1.x installation (pipx, or the `pip install --user` fallback)
with tunnels, a dashboard account file and a running dashboard, then run `install.sh --update`.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not os.environ.get("CLOUDIFY_INSTALL_TESTS"), reason="set CLOUDIFY_INSTALL_TESTS=1")

# Container preparation that a real user's machine already has (bash, a user with sudo).
AS_USER = ("useradd -m -s /bin/bash tester && echo 'tester ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/tester "
           "&& su - tester -c 'bash /src/tests/install/check.sh {mode}'")

CASES = {
    "ubuntu-24.04-root": ("ubuntu:24.04", "bash /src/tests/install/check.sh {mode}"),
    "ubuntu-22.04-root": ("ubuntu:22.04", "bash /src/tests/install/check.sh {mode}"),
    "debian-12-user-sudo-git": ("debian:12", "apt-get update -qq && apt-get install -y -qq sudo >/dev/null && " + AS_USER),
    "fedora-41-root": ("fedora:41", "bash /src/tests/install/check.sh {mode}"),
    # python3 is 3.6 there: the installer must add python3.11 and use it
    "rocky-8-root": ("rockylinux/rockylinux:8", "bash /src/tests/install/check.sh {mode}"),
    # Arch users keep their system up to date (the installer never does a partial upgrade)
    "archlinux-root": ("archlinux:latest", "pacman -Syu --noconfirm >/dev/null && bash /src/tests/install/check.sh {mode}"),
    # a fresh, out-of-date Arch image: the installer must stop with clear instructions instead
    "archlinux-outdated": ("archlinux:latest", "bash /src/install.sh --local /src; echo EXIT=$?"),
    "alpine-3.20-root": ("alpine:3.20", "apk add --no-cache bash >/dev/null && bash /src/tests/install/check.sh {mode}"),
    # python3 (3.9 here) is older than the Python pipx runs on, as on a Mac where Apple's 3.9 comes before
    # Homebrew's in PATH. pipx's shared pip no longer runs on 3.9, so Cloudify must get pipx's own Python
    "pipx-on-newer-python-than-python3": (
        "python:3.9-slim", "apt-get update -qq && apt-get install -y -qq pipx >/dev/null && bash /src/tests/install/check.sh {mode}"),
    # updating from 1.x
    "update-1.x-pip-user-debian-12": (
        "debian:12", "apt-get update -qq && apt-get install -y -qq sudo python3-pip >/dev/null && "
                     "useradd -m -s /bin/bash tester && echo 'tester ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/tester "
                     "&& su - tester -c 'bash /src/tests/install/update.sh pip-user'"),
    "update-1.x-pipx-ubuntu-24.04": (
        "ubuntu:24.04", "apt-get update -qq && apt-get install -y -qq pipx procps >/dev/null "
                        "&& bash /src/tests/install/update.sh pipx"),
    # safety-check.sh: stopping (bash 3.2 as on macOS, BusyBox ps) and removing everything (as a sudo user)
    "safety-kill-bash-3.2": ("bash:3.2", "apk add --no-cache python3 curl >/dev/null && bash /src/tests/install/safety.sh kill"),
    "safety-remove-ubuntu-24.04": (
        "ubuntu:24.04", "apt-get update -qq && apt-get install -y -qq sudo cron curl procps iproute2 >/dev/null && "
                        + AS_USER.replace("check.sh {mode}", "safety.sh remove")),
}


def current_branch():
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    if branch in ("", "HEAD"):  # detached HEAD (CI checks out pull requests this way): give it a name
        branch = "cloudify-install-test"
        subprocess.run(["git", "branch", "-f", branch, "HEAD"], cwd=ROOT, check=True, capture_output=True)
    return branch


@pytest.mark.parametrize("name", list(CASES))
def test_install_script(name):
    image, command = CASES[name]
    if name == "archlinux-outdated":
        result = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/src:ro", image, "sh", "-c", command],
                                capture_output=True, text=True, timeout=1200)
        output = result.stdout + result.stderr
        assert "EXIT=1" in output and "sudo pacman -Syu python python-pipx git" in output, output[-3000:]
        assert "needs root" not in output  # it was run as root: don't blame permissions
        return
    mode = f"git {current_branch()}" if name.endswith("-git") else "local"
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{ROOT}:/src:ro", image, "sh", "-c", command.format(mode=mode)],
        capture_output=True, text=True, timeout=1200)
    output = result.stdout + result.stderr
    marker = ("UPDATE-CHECK-OK" if name.startswith("update-") else
              "SAFETY-CHECK-OK" if name.startswith("safety-") else "INSTALL-CHECK-OK")
    assert result.returncode == 0 and marker in output, output[-4000:]
