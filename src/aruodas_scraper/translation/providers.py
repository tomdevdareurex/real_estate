"""Environment-configurable translation provider factory."""

import os

from aruodas_scraper.translation.base import TranslationProvider
from aruodas_scraper.translation.disabled import DisabledTranslationProvider


def create_translation_provider() -> TranslationProvider:
    """Create the configured provider, defaulting safely to disabled."""
    provider_name = os.getenv("ARUODAS_TRANSLATION_PROVIDER", "disabled").lower()
    if provider_name == "disabled":
        return DisabledTranslationProvider()
    raise ValueError(f"Unsupported translation provider: {provider_name}")
