"""Temporary "quick" tunnels: `cloudflared tunnel --url` -> random *.trycloudflare.com."""

import os
import re
import subprocess
import threading
import time
from typing import Callable, List, Optional, Tuple

from cloudify.core import cloudflared, paths

# A tunnel's own address; api.trycloudflare.com appears in cloudflared's error messages
TRYCLOUDFLARE_RE = re.compile(r'https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com')


def own_config() -> str:
    """A config file of the quick tunnel's own. Without --config cloudflared reads ~/.cloudflared/config.yml
    (often a named tunnel's, with credentials and ingress rules), which Cloudflare doesn't support for
    quick tunnels and which must not decide what a temporary public URL serves."""
    path = os.path.join(paths.data_dir(), "quick-tunnel.yml")
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("# Used by Cloudify for temporary (quick) tunnels instead of ~/.cloudflared/config.yml\n"
                    "no-autoupdate: true\n")
    return path


def start(host: str, port: int, timeout: float = 45, origin: Optional[str] = None,
          cancel: Optional[threading.Event] = None,
          on_process: Optional[Callable[[subprocess.Popen], None]] = None
          ) -> Tuple[Optional[subprocess.Popen], Optional[str], str]:
    """Start a quick tunnel. Returns (process, url, log tail).

    `origin` overrides the forwarded URL, e.g. "ssh://127.0.0.1:2222". Setting `cancel`
    gives up at once (the process is stopped); `on_process` receives the process as soon
    as it exists, so a caller can stop it even while this function is still waiting.
    """
    cmd = [cloudflared.binary(), "tunnel", "--no-autoupdate", "--config", own_config(),
           "--url", origin or f"http://{host}:{port}"]
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True, bufsize=1)
    if on_process:
        on_process(process)

    # cloudflared logs to stderr for as long as it runs. Keep reading it on a
    # background thread: if nobody drains the pipe it fills up and the tunnel freezes.
    found = threading.Event()
    result = {"url": None}
    captured: List[str] = []

    def reader():
        for line in iter(process.stderr.readline, ""):
            if not found.is_set():
                captured.append(line)
                match = TRYCLOUDFLARE_RE.search(line)
                if match:
                    result["url"] = match.group(0)
                    found.set()
        found.set()

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.time() + timeout
    while not found.wait(timeout=0.2):
        if (cancel is not None and cancel.is_set()) or time.time() > deadline:
            break
    if cancel is not None and cancel.is_set():
        if process.poll() is None:
            process.terminate()
        return None, None, "Cancelled"

    if not result["url"]:
        if process.poll() is None:
            process.terminate()
            return None, None, "Timed out waiting for the tunnel URL"
        return None, None, "".join(captured[-20:])
    return process, result["url"], ""
