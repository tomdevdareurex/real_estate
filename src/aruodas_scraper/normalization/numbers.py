"""Lithuanian numeric parsing without fabricated zero values."""

import re

_NUMBER_PATTERN = re.compile(r"[-+]?\d[\d\s\u00a0]*(?:[,.]\d+)?")


def parse_decimal(value: str | None) -> float | None:
    """Parse the first Lithuanian-formatted decimal from text."""
    if value is None:
        return None
    match = _NUMBER_PATTERN.search(value)
    if match is None:
        return None
    normalized = match.group(0).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def parse_integer(value: str | None) -> int | None:
    """Parse the first displayed integer, preserving missing values as null."""
    parsed = parse_decimal(value)
    return int(parsed) if parsed is not None else None
