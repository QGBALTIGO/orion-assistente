"""Compatibility entrypoint for the 15 approved, literal WhatsApp templates.

Formatting delimiters are preserved until delivery by the QR channel.
"""
from app.appointment_message_templates import (
    TEMPLATES,
    STATUS_LABELS,
    label,
    safe_link,
    render_template,
    compose_prefill,
    generic_response,
    is_generic_response,
    compose,
)
