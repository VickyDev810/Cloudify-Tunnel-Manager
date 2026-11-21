"""Web UI accounts (bcrypt hashes in users.json) and bearer tokens."""

import json
import os
import secrets
import stat
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from cloudify.core import jsonfile, paths

TOKEN_TTL_SECONDS = 7 * 24 * 3600

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


_dummy = {"hash": None}


def dummy_hash() -> str:
    """A real bcrypt hash of a random password, for timing-equal checks of unknown user names."""
    if _dummy["hash"] is None:
        _dummy["hash"] = hash_password(secrets.token_hex(16))
    return _dummy["hash"]


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password(password: str):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password must be at most 72 bytes")


def writable_by_others(st) -> bool:
    """Can someone other than the file's owner modify it? Group write counts only when the
    group is not the owner's private group (Ubuntu's umask 002 makes files group-writable for
    a group that has the user as its only member)."""
    if st.st_mode & 0o002:
        return True
    if not st.st_mode & 0o020:
        return False
    try:
        import grp
        import pwd
        group, owner = grp.getgrgid(st.st_gid), pwd.getpwuid(st.st_uid)
    except (ImportError, KeyError):
        return True
    return not (group.gr_name == owner.pw_name and all(m == owner.pw_name for m in group.gr_mem))


class UserStoreError(RuntimeError):
    """users.json exists but can't be read: refuse instead of treating it as "no users yet",
    which would reopen the unauthenticated first-account registration."""


class UserStore:
    """users.json in ~/.cloudflared/cloudify (a users.json in the current
    directory, where older versions kept it, is migrated on first start)."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path
        self._migrated = False
        self.lock = threading.Lock()

    @property
    def path(self) -> Path:
        path = self._path or Path(paths.data_dir()) / "users.json"
        if not self._migrated:
            self._migrated = True
            if not path.exists():
                self._migrate_legacy(Path("users.json"), path)
        return path

    @staticmethod
    def _migrate_legacy(legacy: Path, path: Path):
        """Cloudify 1.x kept users.json in the folder `cloudify serve` ran in. Adopt it only if
        it is clearly that file: a regular file of this user that nobody else can write, holding
        bcrypt records under non-empty names. Otherwise anyone able to drop a users.json there
        could choose the first (administrator) account. Written private (0600), never copied
        with the source's permissions."""
        try:  # check the file that was opened (no symlinks, no swap between check and read)
            fd = os.open(legacy, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            return
        try:
            with os.fdopen(fd) as f:
                st = os.fstat(f.fileno())
                if not stat.S_ISREG(st.st_mode) or writable_by_others(st):
                    return
                if hasattr(os, "getuid") and st.st_uid != os.getuid():
                    return
                users = json.load(f)
        except (OSError, ValueError):
            return
        if not (isinstance(users, dict) and users and all(
                isinstance(name, str) and name == name.strip() and 1 <= len(name) <= 64
                and isinstance(u, dict) and u.get("username", name) == name
                and str(u.get("hashed_password", "")).startswith("$2") for name, u in users.items())):
            return
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
                json.dump(users, f, indent=4)
        except OSError:
            pass

    def load(self) -> Dict[str, Dict]:
        path = self.path
        try:
            with open(path) as f:
                users = json.load(f)
        except FileNotFoundError:
            return {}  # really no users yet
        except (OSError, ValueError) as e:
            raise UserStoreError(f"The user list {path} can't be read ({e}). Fix or restore it; "
                                 "the installer keeps backups in ~/.cloudflared/cloudify/backups.")
        if not isinstance(users, dict):
            raise UserStoreError(f"The user list {path} is damaged. Fix or restore it.")
        return users

    def save(self, users: Dict[str, Dict]):
        jsonfile.write(str(self.path), users, mode=0o600)  # atomic, private from the start

    def count(self) -> int:
        return len(self.load())

    def get(self, username: str) -> Optional[Dict]:
        return self.load().get(username)

    def create(self, username: str, password: str, is_admin: bool) -> Dict:
        users = self.load()
        if username in users:
            raise HTTPException(status_code=400, detail="Username already registered")
        users[username] = {
            "username": username,
            "hashed_password": hash_password(password),
            "is_admin": is_admin,
            "created_at": datetime.now().isoformat(),
            "session_epoch": secrets.token_hex(16),
        }
        self.save(users)
        return users[username]

    def set_password(self, username: str, password: str) -> str:
        """Returns the account's new session epoch: tokens issued before the change stop working."""
        users = self.load()
        users[username]["hashed_password"] = hash_password(password)
        users[username]["password_changed_at"] = datetime.now().isoformat()
        users[username]["session_epoch"] = epoch = secrets.token_hex(16)
        self.save(users)
        return epoch

    def delete(self, username: str):
        users = self.load()
        users.pop(username, None)
        self.save(users)

    def public_list(self):
        return [{"username": u["username"], "is_admin": u.get("is_admin", False),
                 "created_at": u.get("created_at")} for u in self.load().values()]


def session_epoch(user: Dict) -> str:
    """Changes when the password changes or the account is deleted and created again (records from
    older versions have no epoch: their password hash plays that role)."""
    return str(user.get("session_epoch") or user.get("hashed_password") or "")


MAX_TOKENS_PER_USER = 50


class TokenStore:
    """In-memory tokens: restarting the server logs everyone out. Each token remembers the
    account's session epoch it was issued for, so a login that was still running while the
    password changed (or the account was deleted) can't produce a token that outlives it."""

    def __init__(self):
        self._tokens: Dict[str, tuple] = {}   # token -> (username, expires, epoch)
        self._lock = threading.Lock()

    def issue(self, username: str, epoch: str = "") -> str:
        token = secrets.token_hex(32)
        now = time.time()
        with self._lock:
            for t in [t for t, entry in self._tokens.items() if entry[1] < now]:
                del self._tokens[t]
            mine = sorted((entry[1], t) for t, entry in self._tokens.items() if entry[0] == username)
            for _, t in mine[:max(0, len(mine) - MAX_TOKENS_PER_USER + 1)]:
                del self._tokens[t]   # oldest sessions first
            self._tokens[token] = (username, now + TOKEN_TTL_SECONDS, epoch)
        return token

    def entry(self, token: Optional[str]) -> Optional[tuple]:
        """(username, epoch) of a live token."""
        if not token:
            return None
        with self._lock:
            entry = self._tokens.get(token)
            if not entry:
                return None
            if entry[1] < time.time():
                del self._tokens[token]
                return None
            return entry[0], entry[2]

    def username(self, token: Optional[str]) -> Optional[str]:
        entry = self.entry(token)
        return entry[0] if entry else None

    def rebind(self, token: str, epoch: str):
        with self._lock:
            if token in self._tokens:
                username, expires, _ = self._tokens[token]
                self._tokens[token] = (username, expires, epoch)

    def revoke(self, token: str):
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_user(self, username: str, keep: Optional[str] = None):
        with self._lock:
            for token in [t for t, entry in self._tokens.items() if entry[0] == username and t != keep]:
                del self._tokens[token]



# --------------------------------------------------------------------------- #
# First-run setup code
# --------------------------------------------------------------------------- #

_SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I
_setup = {"code": None}


def _normalized(code: Optional[str]) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).upper()


def setup_code() -> str:
    """One-time code that `cloudify serve` prints while no account exists. Creating the first
    account requires it, so only someone who can see the server's console (or `docker logs`)
    can claim a new dashboard, however it is reached (network, proxy, tunnel, other local users).
    CLOUDIFY_SETUP_CODE sets it explicitly (scripted installs, tests)."""
    preset = os.environ.get("CLOUDIFY_SETUP_CODE", "").strip()
    if _normalized(preset):
        return preset
    if _setup["code"] is None:
        raw = "".join(secrets.choice(_SETUP_ALPHABET) for _ in range(10))
        _setup["code"] = f"{raw[:5]}-{raw[5:]}"
    return _setup["code"]


def setup_code_matches(given: Optional[str]) -> bool:
    expected = _normalized(setup_code())
    return bool(expected) and secrets.compare_digest(_normalized(given).encode(), expected.encode())


users = UserStore()
tokens = TokenStore()


def valid_user(token: Optional[str]) -> Optional[Dict]:
    """The account a token belongs to, if the token is live and was issued for the account's current
    session epoch (not before a password change, and not to a deleted account of the same name)."""
    entry = tokens.entry(token)
    user = users.get(entry[0]) if entry else None
    if not user or not secrets.compare_digest(session_epoch(user).encode(), entry[1].encode()):
        return None
    return user


async def current_user(token: str = Depends(oauth2_scheme)) -> Dict:
    user = valid_user(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
