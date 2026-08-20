"""Application-specific exception hierarchy."""


class AruodasScraperError(Exception):
    """Base exception for expected application failures."""


class ConfigurationError(AruodasScraperError):
    """Raised when configuration cannot be loaded or validated."""


class SnapshotError(AruodasScraperError):
    """Raised when an offline HTML snapshot cannot be processed."""


class CheckpointError(AruodasScraperError):
    """Raised when resume state is corrupt or belongs to another run context."""


class RetrievalError(AruodasScraperError):
    """Raised when an authorized remote page cannot be retrieved safely."""


class ProxyAuthenticationError(RetrievalError):
    """Raised when an intercepting proxy demands credentials the client cannot supply."""


class BlockedError(RetrievalError):
    """Raised when the origin rejects the request as automated traffic."""


class DetailIdentityMismatch(AruodasScraperError):
    """Raised when a detail page does not match its discovered listing identity."""
