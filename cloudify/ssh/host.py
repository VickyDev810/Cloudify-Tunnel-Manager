"""`cloudify -ssh host`: share this machine's shell through a temporary tunnel.

1. Generate one-time credentials (client key, host key, password).
2. Start the session SSH server on 127.0.0.1 (random port).
3. Start a Cloudflare quick tunnel to it (random *.trycloudflare.com address;
   no account, open ports or firewall changes needed).
4. Print the access code and password, then wait until Ctrl+C, the time limit,
   or (with --once) the end of the first session.
"""

import asyncio
import getpass
import os
import shlex
import socket
import threading
import time
from typing import Callable, Optional

from cloudify.ssh import credentials as creds_mod
from cloudify.ssh.server import SessionServer

Out = Callable[[str], None]


def _wait_resolvable(hostname: str, timeout: float = 60, cancel: Optional[threading.Event] = None) -> bool:
    """Wait until a brand-new trycloudflare name is published.

    Asks Cloudflare's DNS-over-HTTPS resolver directly: polling the system
    resolver too early would make it cache "no such host" for several minutes,
    which breaks the remote's first connection (same machine or same network).
    """
    import requests
    end = time.time() + timeout
    cancel = cancel or threading.Event()
    while time.time() < end and not cancel.is_set():
        try:
            answer = requests.get("https://cloudflare-dns.com/dns-query", params={"name": hostname, "type": "A"},
                                  headers={"accept": "application/dns-json"}, timeout=5).json()
            if answer.get("Status") == 0 and answer.get("Answer"):
                return True
        except Exception:
            cancel.wait(8)  # no DoH access: just give DNS a moment
            return False
        cancel.wait(2)
    return False


def _stop_tunnel(tunnel):
    """Stop cloudflared now (it otherwise waits up to 30 s for open connections)."""
    if tunnel and tunnel.poll() is None:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=2)
        except Exception:
            tunnel.kill()
            tunnel.wait(timeout=5)


async def _serve(out: Out, ttl_minutes: float, once: bool, local: bool, save_code: Optional[str],
                 ready: Optional[Callable[[dict], None]]) -> int:
    from cloudify.core import quick_tunnel

    from cloudify.core import cloudflared

    if not local and not cloudflared.find_binary():
        out("cloudflared is not installed. Install it (re-run the Cloudify installer, or see "
            "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) "
            "and try again.")
        return 1

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    starting = threading.Event()  # set by Ctrl+C: abandon the startup steps that run in threads
    stopped_by = {"signal": None}
    state = {"tunnel": None}
    creds = creds_mod.generate()

    def on_signal(sig):
        if stop.is_set():  # second Ctrl+C: quit right away
            _stop_tunnel(state["tunnel"])
            os._exit(130)
        stopped_by["signal"] = sig
        starting.set()
        stop.set()

    # Ctrl+C, `kill` or closing the terminal: always stop the tunnel too
    import signal as _signal
    for sig in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(_signal, sig):
            try:
                loop.add_signal_handler(getattr(_signal, sig), lambda s=sig: on_signal(s))
            except (NotImplementedError, RuntimeError):  # Windows: KeyboardInterrupt path below
                pass
    user = getpass.getuser()

    def on_event(kind, info):
        stamp = time.strftime("%H:%M:%S")
        if kind == "connected":
            out(f"[{stamp}] ● remote connected (session {info['session']} · {info['active']} active)")
        elif kind == "disconnected":
            out(f"[{stamp}] ○ remote disconnected ({info['active']} active)")
            if once and info["active"] == 0:
                loop.call_soon(stop.set)

    server = SessionServer(creds, on_event)
    port = await server.start()
    tunnel = None
    try:
        if local:  # testing only: direct TCP, no Cloudflare
            host, code_port = "127.0.0.1", port
        else:
            out("Starting a temporary Cloudflare tunnel... (Ctrl+C to cancel)")
            tunnel, url, error = await loop.run_in_executor(
                None, lambda: quick_tunnel.start("127.0.0.1", port, origin=f"ssh://127.0.0.1:{port}",
                                                 cancel=starting,
                                                 on_process=lambda p: state.update(tunnel=p)))
            state["tunnel"] = tunnel or state["tunnel"]
            if stop.is_set():
                _stop_tunnel(state["tunnel"])
                out("\nStopped before sharing started.")
                return 0
            if not url:
                out(f"Could not start the tunnel: {error.strip() or 'unknown error'}")
                return 1
            host, code_port = url.replace("https://", ""), None
            await loop.run_in_executor(None, lambda: _wait_resolvable(host, cancel=starting))
            if stop.is_set():
                out("\nStopped before sharing started.")
                return 0

        expires_at = time.time() + ttl_minutes * 60
        code = creds_mod.make_access_code(creds, host, user, expires_at, port=code_port)
        if save_code:
            if os.path.lexists(save_code):
                os.remove(save_code)  # recreate: never write through an existing file or symlink
            with os.fdopen(os.open(save_code, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
                f.write(code + "\n")

        minutes = f"{ttl_minutes:g} minute{'s' if ttl_minutes != 1 else ''}"
        out("")
        out(f"  SSH access to {user}@{socket.gethostname()} is ready "
            f"(expires in {minutes}{', closes after one session' if once else ''})")
        out("")
        out("  On the other machine run:   cloudify -ssh remote")
        out("  and enter:")
        out(f"    Access code   {code}")
        out(f"    Password      {creds.password}")
        out("")
        out("  Or run this one-line command there (it includes the password, use it only if you want to):")
        out(f"    cloudify -ssh remote --code {shlex.quote(code)} --password {shlex.quote(creds.password)}")
        out("")
        out("  Anyone with the code and the password gets a shell as this user until you stop sharing.")
        out("  Safest is to send them separately (e.g. code by chat, password by phone) rather than the")
        out("  one-line command. Several people can be connected at the same time.")
        out(f"  Host key: {creds.host_fingerprint}")
        out("")
        out("Waiting for connections... (Ctrl+C to stop sharing)")
        if ready:
            ready({"code": code, "password": creds.password, "host": host, "port": port})

        try:
            await asyncio.wait_for(stop.wait(), timeout=ttl_minutes * 60)
            reason = "Stopped sharing." if stopped_by["signal"] else "Session finished; sharing stopped."
        except asyncio.TimeoutError:
            reason = "Time limit reached; sharing stopped."
        if server.active:
            out(f"\nDisconnecting {server.active} remote session{'s' if server.active != 1 else ''}...")
        await server.close()
        _stop_tunnel(tunnel)
        out(("\n" if stopped_by["signal"] and not server.active else "") + reason + " The access code no longer works.")
        return 0
    finally:
        await server.close()
        _stop_tunnel(tunnel or state["tunnel"])


def run_host(out: Out = print, ttl_minutes: float = 60, once: bool = False, local: bool = False,
             save_code: Optional[str] = None, ready: Optional[Callable[[dict], None]] = None) -> int:
    try:
        return asyncio.run(_serve(out, ttl_minutes, once, local, save_code, ready))
    except KeyboardInterrupt:
        out("\nStopped sharing. The access code no longer works.")
        return 0
