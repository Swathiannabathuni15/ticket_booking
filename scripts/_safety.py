"""Shared guard for the seed / admin scripts."""
import sys
from urllib.parse import urlparse

DEFAULT_PASSWORD = "demo-password-1"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def require_real_admin_password(base_url: str, password: str) -> None:
    """The demo admin password is public (it is in the README). It is fine on your own
    laptop, but never acceptable on a server other people can reach, so refuse to run
    against a non-local address unless ADMIN_PASSWORD is set to something else."""
    host = urlparse(base_url).hostname or ""
    if host not in LOCAL_HOSTS and password == DEFAULT_PASSWORD:
        sys.exit(
            f"Refusing to use the public demo admin password against {host}.\n"
            "Choose a strong password and run again with it set, for example:\n"
            "    ADMIN_PASSWORD='your-own-long-password' python scripts/<this script> <url>"
        )
