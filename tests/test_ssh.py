"""cloudify -ssh host / remote.

Local end-to-end tests run the host with the hidden --local flag (direct TCP
instead of a Cloudflare tunnel); the opt-in test at the bottom goes through a
real quick tunnel (CLOUDIFY_REAL_CLOUDFLARED=/path/to/cloudflared).
"""

import asyncio
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cloudify.ssh import credentials as creds

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not __import__("shutil").which("ssh"),
                                reason="needs a POSIX host and the OpenSSH client")


# --------------------------------------------------------------------------- credentials

def test_password_and_access_code():
    password = creds.new_password()
    assert re.fullmatch(r"[a-z2-9]{4}(-[a-z2-9]{4}){3}", password)
    assert len(set(creds.new_password() for _ in range(50))) == 50

    c = creds.generate()
    code = creds.make_access_code(c, "a.trycloudflare.com", "ai", time.time() + 60)
    assert code.startswith("cfyssh1.") and "PRIVATE" not in code
    payload = creds.open_access_code(code, password=c.password.upper().replace("-", " "))  # forgiving input
    assert payload["host"] == "a.trycloudflare.com" and payload["host_key"] == c.host_public
    assert "PRIVATE KEY" in payload["key"]

    with pytest.raises(creds.AccessCodeError, match="Wrong password"):
        creds.open_access_code(code, "aaaa-bbbb-cccc-dddd")
    tampered = code[:-6] + ("A" if code[-6] != "A" else "B") + code[-5:]
    with pytest.raises(creds.AccessCodeError):
        creds.open_access_code(tampered, c.password)
    with pytest.raises(creds.AccessCodeError, match="start with cfyssh1"):
        creds.open_access_code("hello", c.password)
    with pytest.raises(creds.AccessCodeError, match="incomplete"):
        creds.open_access_code(code[:40], c.password)
    expired = creds.make_access_code(c, "a.trycloudflare.com", "ai", time.time() - 1)
    with pytest.raises(creds.AccessCodeError, match="expired"):
        creds.open_access_code(expired, c.password)


# --------------------------------------------------------------------------- local end to end

def cli_env(**extra):
    return dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]),
                **extra)


class Host:
    def __init__(self, tmp_path, *args, flag=False):
        self.code_file = tmp_path / "code.txt"
        cmd = ["-ssh", "host"] if flag else ["ssh", "host"]
        self.proc = subprocess.Popen([sys.executable, "-u", "-m", "cloudify", *cmd, "--local",
                                      "--save-code", str(self.code_file), *args],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=cli_env())
        self.lines = []
        self.one_liner = None
        while True:
            line = self.proc.stdout.readline()
            assert line, "host exited early:\n" + "".join(self.lines)
            self.lines.append(line)
            if "Password" in line:
                self.password = line.split()[-1]
            if line.strip().startswith("cloudify -ssh remote --code"):
                self.one_liner = line.strip()
                break
        os.set_blocking(self.proc.stdout.fileno(), False)

    def output(self):
        try:
            more = self.proc.stdout.read() or ""
        except (TypeError, OSError):
            more = ""
        self.lines.append(more)
        return "".join(self.lines)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=15)


def remote(host, *args, password=None, stdin=None, flag=False):
    cmd = ["-ssh", "remote"] if flag else ["ssh", "remote"]
    return subprocess.run([sys.executable, "-m", "cloudify", *cmd, "--code-file", str(host.code_file), *args],
                          input=stdin, capture_output=True, text=True, timeout=60,
                          env=cli_env(CLOUDIFY_SSH_PASSWORD=password or host.password))


@pytest.fixture
def host(tmp_path):
    h = Host(tmp_path)
    yield h
    h.stop()


def test_remote_command_and_flag_form(tmp_path):
    h = Host(tmp_path, flag=True)  # `cloudify -ssh host --save-code ...`
    try:
        result = remote(h, "--", "echo hello-$(whoami); tty || true", flag=True)
        assert result.returncode == 0, result.stderr
        assert f"hello-{os.environ.get('USER') or __import__('getpass').getuser()}" in result.stdout
        assert "not a tty" in result.stdout  # single commands don't get a terminal
        assert remote(h, "--", "exit 7").returncode == 7
        assert "remote connected (session 1 · 1 active)" in h.output()
    finally:
        h.stop()


def test_wrong_password_and_tampered_code(host, tmp_path):
    result = remote(host, "--", "true", password="aaaa-bbbb-cccc-dddd")
    assert result.returncode != 0 and "Wrong password" in result.stderr
    host.code_file.write_text(host.code_file.read_text()[:60])
    result = remote(host, "--", "true")
    assert result.returncode != 0 and "incomplete" in result.stderr
    assert "remote connected" not in host.output()


def test_only_the_session_key_is_accepted(host):
    import asyncssh
    port = creds.open_access_code(host.code_file.read_text(), host.password)["port"]

    async def attempt(**kwargs):
        try:
            async with asyncssh.connect("127.0.0.1", port, username="attacker", known_hosts=None, **kwargs) as conn:
                return (await conn.run("echo in")).stdout
        except asyncssh.PermissionDenied as e:
            return f"denied: {e}"

    other_key = asyncssh.generate_private_key("ssh-ed25519")
    assert asyncio.run(attempt(client_keys=[other_key], agent_path=None)).startswith("denied")
    # the password alone is useless: password login isn't even offered
    assert asyncio.run(attempt(client_keys=[], agent_path=None, password=host.password,
                               preferred_auth="password,keyboard-interactive")).startswith("denied")


def test_sftp_roundtrip(host, tmp_path):
    upload, download, target = tmp_path / "up.txt", tmp_path / "down.txt", tmp_path / "on-host.txt"
    upload.write_text("payload-" + str(time.time()))
    batch = f"put {upload} {target}\nget {target} {download}\n"
    result = remote(host, "--sftp", stdin=batch)
    assert result.returncode == 0, result.stderr
    assert download.read_text() == upload.read_text()


def test_interactive_shell_with_resize(host):
    import fcntl, pty, select, struct, termios
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, "-m", "cloudify", "ssh", "remote", "--code-file", str(host.code_file)],
                   cli_env(CLOUDIFY_SSH_PASSWORD=host.password, TERM="xterm-256color"))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    out = b""

    def read(seconds):
        nonlocal out
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return

    read(4)
    os.write(fd, b"tty; stty size\n")
    read(2)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 41, 121, 0, 0))
    os.kill(pid, signal.SIGWINCH)
    read(1)
    os.write(fd, b"stty size; exit 0\n")
    read(3)
    os.waitpid(pid, 0)
    text = out.decode(errors="replace")
    assert "/dev/pts/" in text and "30 100" in text and "41 121" in text, text[-600:]


def test_once_and_ttl_stop_sharing(tmp_path):
    h = Host(tmp_path, "--once")
    assert remote(h, "--", "true").returncode == 0
    h.proc.wait(timeout=15)
    assert "Session finished; sharing stopped." in h.output()

    h = Host(tmp_path, "--ttl", "0.05")  # 3 seconds
    h.proc.wait(timeout=20)
    assert "Time limit reached" in h.output()
    assert remote(h, "--", "true").returncode != 0  # server is gone


def test_sigterm_stops_sharing(host):
    host.proc.send_signal(signal.SIGTERM)
    host.proc.wait(timeout=15)
    assert "Stopped sharing" in host.output()
    assert remote(host, "--", "true").returncode != 0


def _sleeps(marker):
    found = []
    for pid in subprocess.run(["pgrep", "-x", "sleep"], capture_output=True, text=True).stdout.split():
        try:
            if open(f"/proc/{pid}/cmdline").read() == f"sleep\x00{marker}\x00":
                found.append(pid)
        except OSError:
            pass
    return found


def test_one_line_command_always_printed(tmp_path):
    h = Host(tmp_path)
    try:
        out = h.output()
        assert h.one_liner and f"--password {h.password}" in h.one_liner and "includes the password" in out
        assert out.index("Access code") < out.index("Password") < out.index("cloudify -ssh remote --code")
        argv = [sys.executable, "-m"] + shlex.split(h.one_liner)  # exactly what a user pastes
        result = subprocess.run(argv + ["--", "echo pasted"], capture_output=True, text=True, timeout=60, env=cli_env())
        assert result.returncode == 0 and "pasted" in result.stdout, result.stderr
        # During the session the password must not be readable by other users (ps / /proc/PID/cmdline).
        marker = f"onelinerps{os.getpid()}"
        proc = subprocess.Popen(argv + ["--", f"sleep 30 || true # {marker}"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=cli_env())
        try:
            assert wait(lambda: "session 2" in h.output(), 20), h.output()
            cmdline = open(f"/proc/{proc.pid}/cmdline").read()
            assert marker in cmdline and h.password not in cmdline, cmdline
        finally:
            proc.kill()
            proc.wait(timeout=10)
    finally:
        h.stop()


def test_simultaneous_sessions_and_instant_stop(tmp_path):
    h = Host(tmp_path)
    remotes = [subprocess.Popen([sys.executable, "-m", "cloudify", "ssh", "remote", "--code-file", str(h.code_file),
                                 "--", f"echo started-{n}; sleep 8{n}3"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=cli_env(CLOUDIFY_SSH_PASSWORD=h.password)) for n in range(3)]
    try:
        assert wait(lambda: "3 active" in h.output(), 20), h.output()
        assert all(_sleeps(f"8{n}3") for n in range(3))
        started = time.time()
        h.proc.send_signal(signal.SIGINT)  # Ctrl+C
        h.proc.wait(timeout=15)
        assert time.time() - started < 3, "Ctrl+C must stop sharing right away"
        for r in remotes:
            r.wait(timeout=10)
            assert r.returncode == 255 and "stopped sharing" in r.stderr.read()
        out = h.output()
        assert "Disconnecting 3 remote sessions" in out and "Stopped sharing" in out
        assert wait(lambda: not any(_sleeps(f"8{n}3") for n in range(3)), 5), "remote commands left running"
    finally:
        for r in remotes:
            if r.poll() is None:
                r.kill()


def test_vanished_remote_ends_its_command(host):
    client = subprocess.Popen([sys.executable, "-m", "cloudify", "ssh", "remote", "--code-file", str(host.code_file),
                               "--", "sleep 874"], env=cli_env(CLOUDIFY_SSH_PASSWORD=host.password),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    assert wait(lambda: _sleeps("874"), 20)
    os.killpg(client.pid, signal.SIGKILL)  # the remote disappears (network loss, closed laptop...)
    assert wait(lambda: not _sleeps("874"), 10), "command kept running after the remote vanished"
    assert wait(lambda: "(0 active)" in host.output(), 10)


def wait(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


# --------------------------------------------------------------------------- real Cloudflare (opt-in)

REAL = os.environ.get("CLOUDIFY_REAL_CLOUDFLARED")


@pytest.mark.skipif(not REAL, reason="set CLOUDIFY_REAL_CLOUDFLARED to run")
def test_through_a_real_quick_tunnel(tmp_path):
    env = cli_env(PATH=f"{Path(REAL).parent}{os.pathsep}{os.environ['PATH']}")
    code_file = tmp_path / "code.txt"
    proc = subprocess.Popen([sys.executable, "-u", "-m", "cloudify", "-ssh", "host", "--save-code", str(code_file)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        password = None
        deadline = time.time() + 120
        while time.time() < deadline and password is None:
            line = proc.stdout.readline()
            if "Password" in line:
                password = line.split()[-1]
        assert password, "host did not become ready"
        remote_env = dict(env, CLOUDIFY_SSH_PASSWORD=password)
        result = subprocess.run([sys.executable, "-m", "cloudify", "-ssh", "remote", "--code-file", str(code_file),
                                 "--", "echo via-cloudflare"], capture_output=True, text=True, timeout=90, env=remote_env)
        assert result.returncode == 0 and "via-cloudflare" in result.stdout, result.stderr
        assert ".trycloudflare.com" in creds.open_access_code(code_file.read_text(), password)["host"]

        # two live sessions through Cloudflare, then Ctrl+C: everything stops right away
        live = [subprocess.Popen([sys.executable, "-m", "cloudify", "-ssh", "remote", "--code-file", str(code_file),
                                  "--", "echo up; sleep 600"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=remote_env) for _ in range(2)]
        assert all(r.stdout.readline().strip() == "up" for r in live)
        started = time.time()
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=20)
        assert time.time() - started < 5
        for r in live:
            r.wait(timeout=15)
            assert r.returncode == 255
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
    assert not subprocess.run(["pgrep", "-f", "[t]unnel --no-autoupdate (--config [^ ]+ )?--url ssh://"],
                              capture_output=True).stdout


def test_connection_flood_is_capped(host):
    """Everyone arrives through the quick tunnel (from 127.0.0.1): at most MAX_CONNECTIONS may be open."""
    import socket as _socket
    from cloudify.ssh.server import MAX_CONNECTIONS
    port = creds.open_access_code(host.code_file.read_text(), host.password)["port"]
    held = []
    try:
        for _ in range(MAX_CONNECTIONS):
            s = _socket.create_connection(("127.0.0.1", port), timeout=5)
            assert s.recv(64).startswith(b"SSH-2.0-")  # accepted: the server greets it
            held.append(s)
        extra = _socket.create_connection(("127.0.0.1", port), timeout=5)
        data, deadline = b"", time.time() + 5
        while time.time() < deadline:
            chunk = extra.recv(1024)
            if not chunk:
                break                   # closed by the server
            data += chunk
        else:
            raise AssertionError("the connection over the limit was kept open")
        extra.close()
    finally:
        for s in held:
            s.close()
    # once they are gone, a real remote still gets in
    time.sleep(1)
    result = remote(host, "--", "echo", "after-flood")
    assert "after-flood" in result.stdout, result.stdout + result.stderr
