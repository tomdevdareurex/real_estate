"""Content-addressed local HTML cache."""

import hashlib
import os
import secrets
from pathlib import Path


class CacheEntryTooLargeError(ValueError):
    """Raised before an oversized cache entry can be loaded into memory."""


class HtmlCache:
    """Store bytes atomically under a SHA-256 URL key."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._directory / f"{digest}.html"

    def has(self, key: str) -> bool:
        """Whether a read would be served from disk, without reading the entry.

        Callers use this to decide *before* fetching whether the origin will be involved,
        which is what keeps a replay of already-cached pages from being charged against a
        per-IP request budget it never touches.
        """
        return self._path(key).exists()

    def get(self, key: str, max_bytes: int | None = None) -> bytes | None:
        """Return cached bytes when present, optionally enforcing a read limit."""
        path = self._path(key)
        if not path.exists():
            return None
        if max_bytes is None:
            return path.read_bytes()
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if path.stat().st_size > max_bytes:
            raise CacheEntryTooLargeError(f"Cache entry exceeds {max_bytes} bytes")

        chunks: list[bytes] = []
        bytes_read = 0
        with path.open("rb") as file_handle:
            while chunk := file_handle.read(min(64 * 1024, max_bytes - bytes_read + 1)):
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    raise CacheEntryTooLargeError(f"Cache entry exceeds {max_bytes} bytes")
                chunks.append(chunk)
        return b"".join(chunks)

    def put(self, key: str, content: bytes) -> Path:
        """Write cached bytes atomically and return the cache path."""
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        with temporary.open("xb") as file_handle:
            file_handle.write(content)
        os.replace(temporary, path)
        return path

    __all__ = ["CacheEntryTooLargeError", "HtmlCache"]
