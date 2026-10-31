"""Environment-only payment readiness, independent of a provider network call.

A true value is necessary but not sufficient: the checkout endpoint ALSO
requires live webhook inventory or signed delivery history, exact offers,
verified recurring methods, and a server-side owner checkout intent.
Never enable billing from this function alone.
"""
import os


REQUIRED_FLAGS = (
    "ORION_DIRECT_PAYMENTS_ENABLED",
    "ORION_DIRECT_RECURRING_VERIFIED",
    "ORION_WEBHOOK_SECRET_ROTATED_CONFIRMED",
    "ORION_CAKTO_LIFECYCLE_ENABLED",
    "CAKTO_BILLING_LIVE_ENABLED",
)


def direct_environment_ready() -> bool:
    return (
        all(os.getenv(name, "0") == "1" for name in REQUIRED_FLAGS)
        and len(os.getenv("CAKTO_WEBHOOK_SECRET", "")) >= 32
        and bool(os.getenv("CAKTO_CLIENT_ID"))
        and bool(os.getenv("CAKTO_CLIENT_SECRET"))
    )


def webhook_secret_valid() -> bool:
    return len(os.getenv("CAKTO_WEBHOOK_SECRET", "")) >= 32
