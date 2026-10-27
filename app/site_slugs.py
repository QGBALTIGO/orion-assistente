"""Normalize editable site names without changing public URL validation."""
import re
import unicodedata

SLUG_INPUT_LIMIT = 160
SLUG_LIMIT = 80


def normalize_site_slug(value):
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(character for character in decomposed if not unicodedata.category(character).startswith("M"))
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:SLUG_LIMIT].rstrip("-")


def numbered_slug_candidates(slug, *, count=128):
    # Continue an existing short numeric suffix instead of producing -2-2.
    # Very long digit sequences remain part of the business name.
    match = re.fullmatch(r"(.+)-([0-9]{1,9})", slug)
    base, start = (match[1], max(2, int(match[2]) + 1)) if match else (slug, 2)
    for index in range(start, start + count):
        suffix = "-" + str(index)
        yield base[:SLUG_LIMIT - len(suffix)].rstrip("-") + suffix
