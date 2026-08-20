"""Pagination stop-state helpers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaginationState:
    """Immutable result-page traversal state."""

    visited_urls: frozenset[str] = frozenset()
    discovered_ids: frozenset[str] = frozenset()

    def should_stop(self, page_url: str | None, new_ids: frozenset[str]) -> bool:
        """Stop on absent/cyclic pages or a page with no new listing IDs."""
        return page_url is None or page_url in self.visited_urls or not new_ids
