"""Minimal fake of the Cloudflare DNS API (list + delete records) for tests.

Reads/writes the same JSON "Cloudflare" state as tests/fakes/cloudflared:
db["dns"] maps record name -> tunnel id (or another target), db["zones"]
maps zone id -> domain (written by the fake `cloudflared tunnel login`).
"""

import fcntl
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

UUID = re.compile(r"^[0-9a-f-]{36}$")


class FakeCloudflareApi:
    def __init__(self, db_path, valid_tokens=("dns-edit-token",)):
        self.db_path = str(db_path)
        self.valid_tokens = set(valid_tokens)
        self.calls = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                url = urlparse(self.path)
                api.calls.append((method, url.path, self.headers.get("Authorization")))
                token = (self.headers.get("Authorization") or "").replace("Bearer ", "")
                if token not in api.valid_tokens:
                    return self._send(403, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})
                match = re.match(r"^/client/v4/zones/([^/]+)/dns_records(?:/(.+))?$", url.path)
                if not match:
                    return self._send(404, {"success": False, "errors": [{"code": 7003, "message": "Not found"}]})
                zone_id, record_id = match.group(1), match.group(2)
                with api.locked() as db:
                    domain = db.get("zones", {}).get(zone_id)
                    if not domain:
                        return self._send(404, {"success": False, "errors": [{"code": 1001, "message": "Invalid zone"}]})
                    records = [{"id": name, "type": "CNAME", "name": name,
                                "content": f"{target}.cfargotunnel.com" if UUID.match(target) else target}
                               for name, target in db["dns"].items() if name == domain or name.endswith("." + domain)]
                    if method == "GET":
                        query = parse_qs(url.query)
                        if "name" in query:
                            records = [r for r in records if r["name"] == query["name"][0]]
                        return self._send(200, {"success": True, "result": records})
                    record_id = unquote(record_id or "")
                    if record_id not in db["dns"]:
                        return self._send(404, {"success": False, "errors": [{"code": 81044, "message": "Record not found"}]})
                    del db["dns"][record_id]
                    return self._send(200, {"success": True, "result": {"id": record_id}})

            def do_GET(self):
                self._handle("GET")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/client/v4"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    class _Locked:
        def __init__(self, api):
            self.api = api

        def __enter__(self):
            self.handle = open(self.api.db_path + ".lock", "a")
            fcntl.flock(self.handle, fcntl.LOCK_EX)
            try:
                with open(self.api.db_path) as f:
                    self.db = json.load(f)
            except (OSError, ValueError):
                self.db = {}
            self.db.setdefault("dns", {})
            self.db.setdefault("zones", {})
            return self.db

        def __exit__(self, *exc):
            with open(self.api.db_path, "w") as f:
                json.dump(self.db, f)
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()

    def locked(self):
        return self._Locked(self)

    def close(self):
        self.server.shutdown()
