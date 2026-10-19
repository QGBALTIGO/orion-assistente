"""URLs públicas, não adivinháveis, de mídias processadas pelo Orion."""
import re
from uuid import UUID

_PATTERN = re.compile(
    r"^/media/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.webp$"
)


def is_media_url(value: str | None) -> bool:
    """Only Orion's own immutable media paths; never allow relative URL navigation."""
    if not isinstance(value, str):
        return False
    match = _PATTERN.fullmatch(value)
    if not match:
        return False
    try:
        return str(UUID(match[1])) == match[1] and str(UUID(match[2])) == match[2]
    except ValueError:
        return False


def media_key(tenant_id: UUID, media_id: UUID) -> str:
    return f"tenant/{tenant_id}/media/{media_id}.webp"
