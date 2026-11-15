"""One-time credentials for an SSH session and the encrypted "access code".

The host generates, per session:

* a client key pair (Ed25519): the only key the host's SSH server accepts,
* a host key (Ed25519): pinned by the remote, so no "trust this host?" prompt
  and no man-in-the-middle,
* a password: 16 random characters (~79 bits) in groups of four.

The access code bundles the tunnel address, the client *private* key and the
host public key, compressed and encrypted with the password (scrypt + AES-256-GCM).
The remote therefore needs both the code and the password; the host's server
accepts only the key and never accepts passwords, so the password can't be
guessed over the network, and a leaked code can't be decrypted without it.
"""

import base64
import json
import os
import re
import secrets
import time
import zlib
from dataclasses import dataclass
from typing import Dict, Optional

CODE_PREFIX = "cfyssh1."
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i lookalikes
_AAD = b"cloudify-ssh-access-code-v1"


class AccessCodeError(Exception):
    pass


@dataclass
class Credentials:
    client_key: object          # asyncssh.SSHKey (private)
    host_key: object            # asyncssh.SSHKey (private)
    password: str

    @property
    def client_public(self) -> str:
        return self.client_key.export_public_key().decode().strip()

    @property
    def host_public(self) -> str:
        return self.host_key.export_public_key().decode().strip()

    @property
    def host_fingerprint(self) -> str:
        return self.host_key.get_fingerprint("sha256")


def new_password() -> str:
    raw = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize_password(password: str) -> str:
    """Typing-friendly: case, spaces and dashes don't matter."""
    return "".join(c for c in (password or "").lower() if c not in " -\t\r\n")


def generate() -> Credentials:
    import asyncssh
    return Credentials(client_key=asyncssh.generate_private_key("ssh-ed25519"),
                       host_key=asyncssh.generate_private_key("ssh-ed25519"),
                       password=new_password())


def _key(password: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.scrypt(normalize_password(password).encode(), salt=salt, n=2 ** 15, r=8, p=1,
                          dklen=32, maxmem=64 * 1024 * 1024)


def make_access_code(creds: Credentials, host: str, user: str, expires_at: float,
                     port: Optional[int] = None) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    payload = {
        "v": 1,
        "host": host,
        "user": user,
        "key": creds.client_key.export_private_key().decode(),
        "host_key": creds.host_public,
        "expires": int(expires_at),
    }
    if port:
        payload["port"] = port  # direct TCP (local testing only)
    salt, nonce = os.urandom(16), os.urandom(12)
    plain = zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)
    blob = salt + nonce + AESGCM(_key(creds.password, salt)).encrypt(nonce, plain, _AAD)
    # trailing checksum: tells an incomplete copy-paste apart from a wrong password
    return (CODE_PREFIX + base64.urlsafe_b64encode(blob).decode().rstrip("=")
            + f".{zlib.crc32(blob) & 0xffffffff:08x}")


MAX_CODE_LENGTH = 8192        # a real code is about 650 characters
MAX_PAYLOAD_BYTES = 16384     # decompressed

# What a payload may contain before any of it reaches ssh (host names end up in a
# ProxyCommand, the host key in known_hosts, the user in the ssh target).
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_HOST_KEY = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]{68}$")
_PRIVATE_KEY = re.compile(r"^-----BEGIN OPENSSH PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+-----END OPENSSH PRIVATE KEY-----\n?$")


def check_payload(payload) -> Dict:
    """Reject anything a genuine access code can't contain. The host that made the code is
    not trusted to run commands on this machine: its fields must stay plain data."""
    bad = AccessCodeError("The access code contains invalid connection details")
    if not isinstance(payload, dict):
        raise bad
    host, key, host_key = payload.get("host"), payload.get("key"), payload.get("host_key")
    if not (isinstance(host, str) and _HOSTNAME.fullmatch(host)):
        raise bad
    if not (isinstance(host_key, str) and _HOST_KEY.fullmatch(host_key)):
        raise bad
    if not (isinstance(key, str) and len(key) < 4096 and _PRIVATE_KEY.fullmatch(key)):
        raise bad
    port = payload.get("port")
    if port is not None and not (isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
        raise bad
    if not isinstance(payload.get("expires", 0), (int, float)):
        raise bad
    return payload


def open_access_code(code: str, password: str) -> Dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    code = "".join((code or "").split())
    if len(code) > MAX_CODE_LENGTH:
        raise AccessCodeError("That is not a Cloudify SSH access code (it is far too long)")
    if not code.startswith(CODE_PREFIX):
        raise AccessCodeError("That is not a Cloudify SSH access code (it should start with cfyssh1.)")
    damaged = AccessCodeError("The access code is incomplete or damaged (copy the whole line)")
    body, _, checksum = code[len(CODE_PREFIX):].rpartition(".")
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError:
        raise damaged
    if not body or checksum != f"{zlib.crc32(blob) & 0xffffffff:08x}":
        raise damaged
    try:
        salt, nonce, sealed = blob[:16], blob[16:28], blob[28:]
        plain = AESGCM(_key(password, salt)).decrypt(nonce, sealed, _AAD)
        inflater = zlib.decompressobj()
        data = inflater.decompress(plain, MAX_PAYLOAD_BYTES)
        if inflater.unconsumed_tail:  # more than any genuine code holds
            raise damaged
        payload = json.loads(data)
    except InvalidTag:
        raise AccessCodeError("Wrong password, or the access code was changed")
    except (ValueError, zlib.error):
        raise damaged
    if payload.get("v") != 1:
        raise AccessCodeError("This access code needs a newer Cloudify")
    check_payload(payload)
    if payload.get("expires", 0) < time.time():
        raise AccessCodeError("This access code has expired; ask for a new one")
    return payload
