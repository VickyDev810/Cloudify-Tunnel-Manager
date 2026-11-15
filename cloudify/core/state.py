"""Persistent record of the tunnels Cloudify manages.

Stored in ~/.cloudflared/tunnel_manager_state.json (same file and format as
earlier versions). The CLI and the web server (several threads) use it at the
same time: every read-modify-write runs under a lock and files are replaced
atomically (see jsonfile.py).
"""

import os
from datetime import datetime
from typing import Dict, Optional

from cloudify.core import jsonfile, paths


class StateStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or paths.state_file()
        if not os.path.exists(self.path):
            with jsonfile.locked(self.path):
                if not os.path.exists(self.path):
                    self.save({"tunnels": {}, "current_tunnel": None})

    def load(self) -> Dict:
        state = jsonfile.read(self.path, {})
        if not isinstance(state, dict):
            state = {}
        if not isinstance(state.get("tunnels"), dict):
            state["tunnels"] = {}
        state.setdefault("current_tunnel", None)
        return state

    def save(self, state: Dict):
        state["last_updated"] = datetime.now().isoformat()
        jsonfile.write(self.path, state)

    def locked(self):
        """Hold this across a read-modify-write that spans several calls."""
        return jsonfile.locked(self.path)

    # -- tunnels ------------------------------------------------------------

    def tunnels(self) -> Dict[str, Dict]:
        return self.load()["tunnels"]

    def get(self, name: str) -> Dict:
        return self.tunnels().get(name, {})

    def register(self, name: str, make_current: bool = True):
        with self.locked():
            state = self.load()
            state["tunnels"].setdefault(name, {
                "created_at": datetime.now().isoformat(),
                "config_file": paths.tunnel_config(name),
                "routes": [],
                "auto_start": False,
                "status": "unknown",
            })
            if make_current:
                state["current_tunnel"] = name
            self.save(state)

    def update(self, name: str, info: Dict):
        with self.locked():
            state = self.load()
            if name in state["tunnels"]:
                state["tunnels"][name].update(info)
                self.save(state)

    def unregister(self, name: str):
        with self.locked():
            state = self.load()
            if name in state["tunnels"]:
                del state["tunnels"][name]
                if state["current_tunnel"] == name:
                    state["current_tunnel"] = None
                self.save(state)

    # -- current tunnel -----------------------------------------------------

    def current(self) -> Optional[str]:
        return self.load().get("current_tunnel")

    def set_current(self, name: Optional[str]):
        with self.locked():
            state = self.load()
            state["current_tunnel"] = name
            self.save(state)
