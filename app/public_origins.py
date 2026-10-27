"""Server-configured public origins; request forwarding headers grant no trust."""
import os
import ipaddress
import re
from urllib.parse import urlsplit

RAILWAY_PUBLIC_ORIGIN = "https://orion-assistente-production.up.railway.app"


def checked_public_origin(value):
    if not isinstance(value, str) or not value or any(ord(c) <= 32 for c in value):
        raise ValueError("Invalid configured public origin")
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid configured public origin") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or not parsed.netloc
            or "\\" in parsed.netloc or port is not None and not 1 <= port <= 65535):
        raise ValueError("Invalid configured public origin")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if len(parsed.hostname) > 253 or not re.fullmatch(label + r"(?:\." + label + r")*", parsed.hostname, re.I):
            raise ValueError("Invalid configured public origin")
    return "https://" + parsed.netloc.lower()


def canonical_public_origin():
    return checked_public_origin(os.getenv("PANEL_PUBLIC_URL", RAILWAY_PUBLIC_ORIGIN).strip())


def legacy_public_origins():
    """Compatibility is opt-in and exact; never accept wildcards or host suffixes."""
    raw = os.getenv("PANEL_LEGACY_PUBLIC_ORIGINS", "").strip()
    if not raw:
        return frozenset()
    values = raw.split(",")
    if len(values) > 8 or any(not item.strip() or "*" in item for item in values):
        raise ValueError("Invalid configured legacy public origins")
    return frozenset(checked_public_origin(item.strip()) for item in values)
