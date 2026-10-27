"""Temporary platform-wide policy for the public Orion credit.

Saved choices and publication receipts stay intact while hiding is unavailable.
No tenant, role or plan is exempt from this lock.
"""

ORION_BRAND_HIDING_LOCKED = True


def effective_hide_orion_brand(requested):
    return not ORION_BRAND_HIDING_LOCKED and requested is True
