"""Input validation for tunnel names, domains and origin hosts."""

import re
from typing import Optional

# Names for NEW tunnels end up in file names, systemd unit names, launchd
# labels, scheduled task names and process-match patterns, so keep them simple.
TUNNEL_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$')

# Tunnels that already exist (created elsewhere and adopted, or by older
# versions) may have other names; only reject characters that are unsafe in a
# file path or could break out of a command line.
_UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f/\\"\'`$;&|<>*?]')

# Public hostname for a route, e.g. app.example.com or *.example.com
DOMAIN_RE = re.compile(
    r'^(\*\.)?(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$'
)

# Origin host behind a route: hostnames, IPv4, Docker container names (which
# may contain "_") and bracketed IPv6 such as [::1].
SERVICE_HOST_RE = re.compile(
    r'^([A-Za-z0-9_]([A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?|\[[0-9A-Fa-f:.]+\])$'
)

NEW_TUNNEL_NAME_HELP = ("Use letters, numbers, '-', '_' or '.' "
                        "(max 63 characters, must start with a letter or number).")


# All patterns are used with fullmatch: with re.match, "$" also matches before a trailing newline.

def is_valid_tunnel_name(name: Optional[str]) -> bool:
    """Strict check used when creating a new tunnel."""
    return isinstance(name, str) and bool(TUNNEL_NAME_RE.fullmatch(name))


def is_safe_tunnel_name(name: Optional[str]) -> bool:
    """Looser check for managing tunnels that already exist. A leading '-' is refused: the
    name is passed to cloudflared as an argument and would be read as an option."""
    return (isinstance(name, str) and bool(name) and len(name) <= 255 and name not in (".", "..")
            and not name.startswith("-")
            and name == name.strip() and not _UNSAFE_NAME_RE.search(name))


def is_valid_domain(domain: Optional[str]) -> bool:
    return isinstance(domain, str) and bool(DOMAIN_RE.fullmatch(domain))


def is_valid_service_host(host: Optional[str]) -> bool:
    return isinstance(host, str) and bool(SERVICE_HOST_RE.fullmatch(host))


def strip_scheme(value: Optional[str]) -> str:
    """'https://App.example.com/' -> 'App.example.com'"""
    return re.sub(r'^https?://', '', (value or "").strip()).rstrip('/')


def parse_port(value) -> Optional[int]:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None
