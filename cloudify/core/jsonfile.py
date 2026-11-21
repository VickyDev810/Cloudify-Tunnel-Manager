"""Small JSON files shared by the CLI, the web server's threads and other processes.

* ``write`` replaces the file atomically through a uniquely named temporary file, so
  readers never see a half-written file and concurrent writers never share a temp file.
* ``locked`` serializes read-modify-write sequences across threads and processes
  (``fcntl.flock`` on ``<file>.lock``; ``msvcrt`` on Windows). It is re-entrant within
  a thread, so a locked method may call another locked method.
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Iterator, Optional

_thread_locks = {}
_guard = threading.Lock()
_local = threading.local()


@contextlib.contextmanager
def locked(path: str) -> Iterator[None]:
    key = os.path.abspath(path)
    depth = getattr(_local, "depth", None)
    if depth is None:
        depth = _local.depth = {}
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    with _guard:
        thread_lock = _thread_locks.setdefault(key, threading.Lock())
    with thread_lock:
        os.makedirs(os.path.dirname(key), exist_ok=True)
        fd = os.open(key + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_fd(fd)
            depth[key] = 1
            try:
                yield
            finally:
                depth[key] = 0
        finally:
            os.close(fd)  # releases the OS lock


def _lock_fd(fd: int):
    if sys.platform == "win32":
        import msvcrt
        deadline = time.time() + 30
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.05)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


class JsonFileError(ValueError):
    """The file exists but can't be read right now (permissions, too many open files...)."""


def read(path: str, default: Any) -> Any:
    """The file's JSON, or ``default`` if it doesn't exist.

    A file that exists but can't be read raises JsonFileError: reading it as empty would let
    the next write replace everything in it. A file that isn't valid JSON is copied to
    ``<file>.corrupt`` first, so a later write can't silently destroy what is left of it.
    """
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        return default
    except OSError as e:
        raise JsonFileError(f"{path} can't be read right now ({e.strerror or e}); nothing was changed. Try again.")
    try:
        return json.loads(text)
    except ValueError:
        backup = path + ".corrupt"
        if not os.path.exists(backup):
            try:
                shutil.copyfile(path, backup)
                os.chmod(backup, 0o600)
            except OSError:
                pass
        print(f"Warning: {path} is not valid JSON (copy kept as {backup})", file=sys.stderr)
        return default


def write(path: str, data: Any, mode: Optional[int] = 0o600):
    """Write JSON atomically (new file is private to the user unless ``mode`` says otherwise)."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
