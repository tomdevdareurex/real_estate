"""Consistent English application logging."""

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure concise timestamped console logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
