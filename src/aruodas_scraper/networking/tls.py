"""Certificate trust resolution for TLS-intercepting corporate proxies."""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path

from aruodas_scraper.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

# httpx verifies against certifi and, unlike requests/curl/node, ignores every one of these.
# Corporate images commonly set them, so honouring them keeps the scraper working with no
# extra setup on machines already configured for a TLS-intercepting proxy.
_CA_ENVIRONMENT_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

VerifyOption = ssl.SSLContext | bool


@dataclass(frozen=True, slots=True)
class TlsTrust:
    """Resolved certificate trust plus the origin that supplied it."""

    verify: VerifyOption
    source: str
    bundle: Path | None = None


def resolve_tls_trust(explicit: Path | str | None = None) -> TlsTrust:
    """Resolve the certificate trust httpx should use, preferring the most specific source.

    Args:
        explicit: Certificate bundle configured for this run, if any.

    Returns:
        Trust settings suitable for ``httpx.Client(verify=...)`` and a human-readable source.

    Raises:
        ConfigurationError: If an explicitly configured bundle does not exist.
    """
    if explicit is not None:
        bundle = Path(explicit)
        if not bundle.is_file():
            raise ConfigurationError(f"Configured CA bundle does not exist: {bundle}")
        return _trust_from_bundle(bundle, f"configured ca_bundle ({bundle})")

    for variable in _CA_ENVIRONMENT_VARIABLES:
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        bundle = Path(value)
        if not bundle.is_file():
            logger.warning("%s points at a missing CA bundle and was ignored: %s", variable, bundle)
            continue
        return _trust_from_bundle(bundle, f"{variable} ({bundle})")

    context = _operating_system_trust()
    if context is not None:
        return TlsTrust(verify=context, source="operating system trust store (truststore)")

    return TlsTrust(verify=True, source="certifi defaults")


def _trust_from_bundle(bundle: Path, source: str) -> TlsTrust:
    """Build a verification context from a certificate file, reporting unusable bundles."""
    try:
        context = ssl.create_default_context(cafile=str(bundle))
    except (OSError, ssl.SSLError) as error:
        raise ConfigurationError(f"CA bundle could not be loaded: {bundle} ({error})") from error
    return TlsTrust(verify=context, source=source, bundle=bundle)


def _operating_system_trust() -> ssl.SSLContext | None:
    """Return an OS-trust-store context when truststore is installed, else None."""
    try:
        import truststore
    except ImportError:
        return None
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


__all__ = ["TlsTrust", "VerifyOption", "resolve_tls_trust"]
