"""Self-hosted API + hosted camera page. Needs the [server] extra."""

from id_verification.server.app import create_app
from id_verification.server.db import migrate

__all__ = ["create_app", "migrate"]
