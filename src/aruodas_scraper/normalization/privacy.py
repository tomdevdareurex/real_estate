"""Data-minimization helpers for contact details embedded in listing text."""

import re

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)(?:\+370|8)[\s-]?(?:\d[\s-]?){8}(?!\w)")


def redact_contact_details(value: str | None) -> str | None:
    """Redact public email addresses and Lithuanian phone numbers from text."""
    if value is None:
        return None
    without_email = _EMAIL.sub("[REDACTED_EMAIL]", value)
    return _PHONE.sub("[REDACTED_PHONE]", without_email)
