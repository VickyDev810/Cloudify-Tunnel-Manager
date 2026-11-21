"""Filesystem locations. Kept identical to earlier versions so existing
installs (state, configs, users) keep working after upgrading."""

import os
import re
import stat

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


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


def collect_credentials(tunnel_id: str) -> str:
    """Make sure the tunnel's credentials are where `cloudflared tunnel run` looks for them
    (credentials_file), and return that path.

    `cloudflared tunnel create` writes them next to the certificate it was given. For domains connected
    through Cloudify that is Cloudify's certs folder, which `tunnel run` never searches, so the tunnel
    could not start ("tunnel credentials file not found"). This also repairs tunnels created that way
    by earlier versions."""
    target = credentials_file(tunnel_id)
    if isinstance(tunnel_id, str) and _UUID.fullmatch(tunnel_id) and not os.path.lexists(target):
        stray = os.path.join(data_dir(), "certs", f"{tunnel_id}.json")
        if os.path.isfile(stray) and not os.path.islink(stray):
            try:
                os.replace(stray, target)
            except OSError:
                pass
    return target


def cert_file() -> str:
    return os.path.join(config_dir(), "cert.pem")


def tunnel_log(tunnel_name: str) -> str:
    return os.path.join(log_dir(), f"{tunnel_name}.log")
