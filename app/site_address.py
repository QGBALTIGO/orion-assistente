"""Structured draft address extensions and the public address line.

The original street/legacy text stays in management_site_preferences.address.
Only the additional address fields use the existing tenant-scoped JSON settings.
Published readers use their immutable snapshot, never these draft settings.
"""
import json

from sqlalchemy import text

ADDRESS_DEFAULTS = {
    "address_number": None,
    "address_complement": None,
    "neighborhood": None,
    "address_no_number": False,
}
ADDRESS_FIELDS = frozenset(ADDRESS_DEFAULTS)


def address_fields(value):
    source = value if isinstance(value, dict) else {}
    result = {key: source.get(key, default) for key, default in ADDRESS_DEFAULTS.items()}
    result["address_no_number"] = result["address_no_number"] is True
    if result["address_no_number"]:
        result["address_number"] = None
    return result


def merge_address_fields(current, values, provided):
    result = {**address_fields(current), **{key: values[key] for key in ADDRESS_FIELDS & set(provided)}}
    if "address_no_number" in provided and values["address_no_number"]:
        result["address_number"] = None
    elif "address_number" in provided and values["address_number"]:
        result["address_no_number"] = False
    return address_fields(result)


def address_display(site):
    """Compose only explicit fields; never infer a number from legacy text."""
    def value(key):
        raw = site.get(key)
        return raw.strip() if isinstance(raw, str) else ""
    number = "s/n" if site.get("address_no_number") is True else value("address_number")
    street = ", ".join(part for part in (value("address"), number) if part)
    return " — ".join(part for part in (street, value("address_complement"), value("neighborhood")) if part) or None


async def read_address_fields(db, tenant):
    stored = (await db.execute(text("""SELECT preferences->'site_address' FROM management_preferences
        WHERE tenant_id=:tenant"""), {"tenant": tenant})).scalar_one_or_none()
    return address_fields(stored)


async def save_address_fields(db, tenant, values):
    # A namespace update composes atomically with business/automation updates.
    # The site's optimistic version owns these fields; do not invalidate an
    # unrelated automation approval by incrementing its preferences version.
    await db.execute(text("""INSERT INTO management_preferences(tenant_id,preferences)
        VALUES(:tenant,jsonb_build_object('site_address',CAST(:address AS jsonb)))
        ON CONFLICT(tenant_id) DO UPDATE SET
            preferences=jsonb_set(management_preferences.preferences,'{site_address}',CAST(:address AS jsonb),true)
    """), {"tenant": tenant, "address": json.dumps(address_fields(values))})
