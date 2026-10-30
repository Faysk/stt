"""Admin-only SSH inventory. Connection metadata never goes to the public site.

An SSH destination is a management endpoint, NOT proof that a compatible
transcription worker or ASR runtime is running on that device.
"""
import re
from server.config import getenv

_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z", re.ASCII)
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z", re.ASCII)


def ssh_targets():
    host = (getenv("STT_S7_SSH_HOST", "") or "").strip()
    user = (getenv("STT_S7_SSH_USER", "") or "").strip()
    port_text = (getenv("STT_S7_SSH_PORT", "8022") or "").strip()
    try:
        port = int(port_text)
    except ValueError:
        port = 0
    valid = (
        bool(_HOST.fullmatch(host)) and
        bool(_USER.fullmatch(user)) and
        1 <= port <= 65535 and
        ".." not in host and
        not host.endswith(".")
    )
    return [{
        "id": "s7-edge",
        "label": "Galaxy S7 Edge",
        "configured": valid,
        "command": f"ssh -p {port} {user}@{host}" if valid else None,
    }]
