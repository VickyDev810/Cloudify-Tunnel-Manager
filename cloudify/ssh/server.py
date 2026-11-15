"""Small SSH server for one sharing session (built on asyncssh).

* Listens on 127.0.0.1 only; the outside world reaches it through the
  Cloudflare tunnel.
* Accepts exactly one client key (generated for this session); password,
  keyboard-interactive and host-based login are disabled.
* Gives a login shell as the current user, with a real terminal (PTY) when the
  client asks for one, runs single commands, and supports sftp/scp.
* Port, agent and X11 forwarding are refused.
"""

import asyncio
import os
import struct
import subprocess
import sys
from typing import Callable, Optional

IS_WINDOWS = sys.platform == "win32"


def login_shell() -> str:
    if IS_WINDOWS:
        return os.environ.get("COMSPEC", "cmd.exe")
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    except (ImportError, KeyError):
        return os.environ.get("SHELL", "/bin/sh")


def _set_winsize(fd: int, cols: int, rows: int):
    import fcntl  # POSIX only (PTY sessions don't run on Windows)
    import termios
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows or 24, cols or 80, 0, 0))
    except OSError:
        pass


def _hang_up(child):
    """Like sshd: when the connection goes away, send SIGHUP to the session's process group."""
    import signal
    try:
        os.killpg(child.pid, signal.SIGHUP)
    except (OSError, AttributeError):
        try:
            child.terminate()
        except OSError:
            pass


async def _run_with_pty(process, children: set) -> int:
    """Interactive shell (or command) on a pseudo-terminal, forwarding resizes."""
    import asyncssh
    import fcntl
    import pty
    import termios

    master, slave = pty.openpty()
    cols, rows = process.get_terminal_size()[:2]
    _set_winsize(master, cols, rows)
    env = dict(os.environ, TERM=process.get_terminal_type() or "xterm-256color")
    shell = login_shell()
    argv = [shell, "-l"] if not process.command else [shell, "-c", process.command]

    def make_controlling_tty():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    child = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
                             cwd=os.path.expanduser("~"), preexec_fn=make_controlling_tty)
    os.close(slave)
    children.add(child)
    loop = asyncio.get_running_loop()
    output_done = loop.create_future()

    def on_output():
        try:
            data = os.read(master, 65536)
        except OSError:  # EIO once the shell exits
            data = b""
        if data:
            process.stdout.write(data)
        elif not output_done.done():
            loop.remove_reader(master)
            output_done.set_result(None)

    loop.add_reader(master, on_output)

    async def forward_input():
        while True:
            try:
                data = await process.stdin.read(65536)
            except asyncssh.TerminalSizeChanged as change:
                _set_winsize(master, change.width, change.height)
                continue
            except (asyncssh.BreakReceived, asyncssh.SignalReceived):
                continue
            if not data:
                break
            os.write(master, data)
        # channel closed (remote left or connection dropped): hang up the shell
        _hang_up(child)

    input_task = asyncio.ensure_future(forward_input())
    try:
        await output_done
    finally:
        input_task.cancel()
        if child.poll() is None:
            _hang_up(child)
        code = await loop.run_in_executor(None, child.wait)
        children.discard(child)
        os.close(master)
    return code


async def _run_with_pipes(process, children: set) -> int:
    """Non-interactive command (or a shell without a terminal, e.g. on Windows)."""
    shell = login_shell()
    if IS_WINDOWS:
        argv = [shell, "/c", process.command] if process.command else [shell]
    else:
        argv = [shell, "-c", process.command] if process.command else [shell, "-l"]
    child = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE,
                                                 cwd=os.path.expanduser("~"),
                                                 start_new_session=not IS_WINDOWS)
    children.add(child)

    async def pump(reader, writer):
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)

    async def feed():
        try:
            while True:
                data = await process.stdin.read(65536)
                if not data:
                    break
                child.stdin.write(data)
                await child.stdin.drain()
        except Exception:
            pass
        finally:
            try:
                child.stdin.close()
            except Exception:
                pass

    feeder = asyncio.ensure_future(feed())
    output = asyncio.ensure_future(asyncio.gather(pump(child.stdout, process.stdout),
                                                  pump(child.stderr, process.stderr)))
    # If the remote vanishes (network loss, client killed), end the command like sshd would.
    conn = process.get_extra_info("connection")
    gone = asyncio.ensure_future(conn.wait_closed()) if conn else asyncio.get_running_loop().create_future()
    try:
        await asyncio.wait([output, gone], return_when=asyncio.FIRST_COMPLETED)
        if not output.done():
            _hang_up(child)
            output.cancel()
        await asyncio.gather(output, return_exceptions=True)  # collect the cancelled readers quietly
        return await child.wait()
    finally:
        gone.cancel()
        feeder.cancel()
        if child.returncode is None:
            _hang_up(child)
        children.discard(child)


MAX_CONNECTIONS = 16  # remotes connected at once (a shell and an SFTP session each count)

class SessionServer:
    """Runs the SSH server; `on_event(kind, info)` reports connections to the host UI.

    Any number of remotes can be connected at the same time (each gets its own
    shell); `close()` disconnects all of them and ends their processes.
    """

    def __init__(self, creds, on_event: Callable[[str, dict], None]):
        self.creds = creds
        self.on_event = on_event
        self.server = None
        self.port: Optional[int] = None
        self.connections = set()   # authenticated connections
        self.children = set()      # shell/command processes of all sessions
        self.sessions = 0

    @property
    def active(self) -> int:
        return len(self.connections)

    async def start(self, port: int = 0) -> int:
        import asyncssh

        open_connections = {"count": 0}

        class Server(asyncssh.SSHServer):
            # Everyone reaches this listener through the public quick tunnel, i.e. from 127.0.0.1:
            # cap how many connections (mostly not yet authenticated) may be open at once.
            def connection_made(self, conn):
                open_connections["count"] += 1
                if open_connections["count"] > MAX_CONNECTIONS:
                    conn.close()

            def connection_lost(self, exc):
                open_connections["count"] -= 1

            def begin_auth(self, username):
                return True  # authentication is always required

            # every other auth method and forwarding stays at asyncssh's default: refused
            def password_auth_supported(self):
                return False

            def kbdint_auth_supported(self):
                return False

            def validate_gss_principal(self, username, user_principal, host_principal):
                return False  # Kerberos must never stand in for the session key

        async def handle(process):
            interactive = process.get_terminal_type() is not None and not IS_WINDOWS
            try:
                runner = _run_with_pty if interactive else _run_with_pipes
                code = await runner(process, self.children)
            except Exception as e:  # never take the server down with one session
                process.stderr.write(f"cloudify: session error: {e}\r\n".encode())
                code = 1
            process.exit(code)

        home = os.path.expanduser("~").encode()

        class HomeSFTPServer(asyncssh.SFTPServer):
            """Relative SFTP/SCP paths start in the home folder (like sshd), not in the
            folder `cloudify -ssh host` happened to be started from."""
            def map_path(self, path: bytes) -> bytes:
                if not os.path.isabs(path):
                    path = os.path.join(home, path) if path not in (b"", b".") else home
                return super().map_path(path)

        authorized = asyncssh.import_authorized_keys(self.creds.client_public + "\n")
        self.server = await asyncssh.create_server(
            Server, "127.0.0.1", port,
            server_host_keys=[self.creds.host_key],
            authorized_client_keys=authorized,
            process_factory=handle,
            sftp_factory=HomeSFTPServer, allow_scp=True,
            encoding=None, line_editor=False,
            agent_forwarding=False, x11_forwarding=False,
            gss_auth=False, gss_kex=False,  # asyncssh enables GSS-API by default where gssapi is installed
            login_timeout=20, keepalive_interval=30,
            acceptor=self._accepted,  # called once a client has authenticated
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    def _accepted(self, conn):
        self.connections.add(conn)
        self.sessions += 1
        self.on_event("connected", {"session": self.sessions, "active": self.active})

        def gone(_):
            if conn in self.connections:
                self.connections.discard(conn)
                self.on_event("disconnected", {"active": self.active})

        asyncio.ensure_future(conn.wait_closed()).add_done_callback(gone)

    async def close(self, timeout: float = 3):
        """Stop listening, disconnect every remote and end their processes."""
        if self.server:
            self.server.close()
        for conn in list(self.connections):
            conn.close()
        for child in list(self.children):
            _hang_up(child)
        waits = [c.wait_closed() for c in list(self.connections)]
        if waits:
            try:
                await asyncio.wait_for(asyncio.gather(*waits, return_exceptions=True), timeout)
            except asyncio.TimeoutError:
                for conn in list(self.connections):
                    conn.abort()
