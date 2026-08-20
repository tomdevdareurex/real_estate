"""Translation provider interface."""

from typing import Protocol


class TranslationProvider(Protocol):
    """Translate Lithuanian text to English without summarizing it."""

    def translate_lt_to_en(self, text: str) -> str | None: ...
