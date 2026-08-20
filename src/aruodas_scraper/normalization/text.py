"""Text cleanup helpers that preserve Lithuanian content."""

import re


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace without translating or paraphrasing source text."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
    return cleaned or None


def clean_label(value: str) -> str:
    """Normalize a displayed attribute label for mapping lookup."""
    return (clean_text(value) or "").rstrip(":").strip()
