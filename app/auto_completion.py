"""Compatibility entry points: secure database-owned scanner and tenant-local completion.

The cross-tenant scanner is a SECURITY DEFINER function that exposes only
tenant and appointment UUIDs to app_support, never customer or booking data.
"""
from app.auto_completion_worker import (
    enabled as auto_completion_enabled,
    scan_due_auto_completions as scan_due_appointments,
    complete_due_appointment as process_due_appointment,
)
