"""Default no-op translation provider."""


class DisabledTranslationProvider:
    """Return null so source text is never invented or paraphrased."""

    def translate_lt_to_en(self, text: str) -> None:
        """Leave English text unavailable when translation is disabled."""
        del text
        return None
