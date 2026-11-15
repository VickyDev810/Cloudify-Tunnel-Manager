"""Filesystem locations. Kept identical to earlier versions so existing
installs (state, configs, users) keep working after upgrading."""

import os
import stat


def private_dir(path: str) -> str:
    """Create `path` readable by the owner only (0700), and tighten it if it was created
    with the default umask: it holds certificates, tunnel credentials and API tokens."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        st = os.stat(path)
        if os.name == "posix" and st.st_uid == os.getuid() and st.st_mode & 0o077:
            os.chmod(path, stat.S_IMODE(st.st_mode) & 0o700)
    except OSError:
        pass
    return path


def config_dir() -> str:
    """cloudflared's own directory: cert.pem, tunnel credentials, configs."""
    return private_dir(os.path.expanduser("~/.cloudflared"))


def data_dir() -> str:
    """Cloudify's private files: users, login status, logs."""
    return private_dir(os.path.join(config_dir(), "cloudify"))


def log_dir() -> str:
    path = os.path.join(config_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def state_file() -> str:
    return os.path.join(config_dir(), "tunnel_manager_state.json")


def tunnel_config(tunnel_name: str) -> str:
    return os.path.join(config_dir(), f"config-{tunnel_name}.yml")


def credentials_file(tunnel_id: str) -> str:
    return os.path.join(config_dir(), f"{tunnel_id}.json")


def cert_file() -> str:
    return os.path.join(config_dir(), "cert.pem")


def tunnel_log(tunnel_name: str) -> str:
    return os.path.join(log_dir(), f"{tunnel_name}.log")
