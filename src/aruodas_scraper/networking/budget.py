"""Burst/cooldown scheduling against a per-IP request-count budget.

PerimeterX serves this client a finite *number* of requests per source IP, then refuses
everything until the block lapses after roughly 20-25 minutes. Pacing does not lift that
ceiling: raising the gap between requests from 13s to 45s still stopped the run at 16
pages. The only two levers that work are asking for fewer pages per record, and treating
the ceiling as a budget to be spent in bursts separated by cooldowns.

This module owns the second lever. It stops a burst the moment the origin refuses, waits
for the block to lapse, and then resumes slightly below the ceiling it last observed, so
subsequent bursts often never trip the block at all.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Slightly under the observed 20-25 minute block TTL is a gamble; slightly over is not.
DEFAULT_COOLDOWN_SECONDS = 1500.0

# Four cooldowns is a little under two hours of waiting, which is about as long as a run can
# take before it is likelier to be interrupted than to finish.
DEFAULT_MAX_COOLDOWNS = 4


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Bounds on how long a run may spend waiting out blocks."""

    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    max_cooldowns: int = DEFAULT_MAX_COOLDOWNS
    max_runtime_seconds: float | None = None
    # Stop this many requests short of the last observed ceiling. Tripping the block costs
    # a wasted request and restarts the TTL, so it is worth giving up a little yield.
    safety_margin: int = 2
    # Never predict a ceiling below this: a too-pessimistic estimate stalls the crawl in
    # cooldowns that buy nothing.
    minimum_burst: int = 5

    def __post_init__(self) -> None:
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        if self.max_cooldowns < 0:
            raise ValueError("max_cooldowns cannot be negative")
        if self.max_runtime_seconds is not None and self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.safety_margin < 0:
            raise ValueError("safety_margin cannot be negative")
        if self.minimum_burst < 1:
            raise ValueError("minimum_burst must be at least one")


class RequestBudget:
    """Decides whether the next request may be sent, cooling down when it may not.

    The caller reports the outcome of every request through :meth:`record_success` and
    :meth:`record_block`, and asks :meth:`request_permitted` before each one. That method
    blocks for the cooldown when the current burst is spent, and returns ``False`` only
    when the run itself is over.
    """

    def __init__(
        self,
        policy: BudgetPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy or BudgetPolicy()
        self._sleep = sleeper
        self._clock = clock
        self._started = clock()
        self._burst_successes = 0
        self._blocked = False
        self._observed_ceiling: int | None = None
        self._cooldowns_used = 0
        self._stop_reason: str | None = None

    @property
    def cooldowns_used(self) -> int:
        return self._cooldowns_used

    @property
    def observed_ceiling(self) -> int | None:
        """Successful requests the origin served before it last refused."""
        return self._observed_ceiling

    @property
    def stop_reason(self) -> str | None:
        """Why the budget stopped permitting requests, or ``None`` while it still does."""
        return self._stop_reason

    @property
    def burst_is_spent(self) -> bool:
        """Whether the next permitted request has to wait out a cooldown first.

        Callers holding unsaved work use this to bank it before the wait, since a cooldown is
        long enough that a run is far more likely to be interrupted during one than outside it.
        """
        return self._burst_is_spent()

    def _predicted_ceiling(self) -> int | None:
        if self._observed_ceiling is None:
            return None
        return max(self._policy.minimum_burst, self._observed_ceiling - self._policy.safety_margin)

    def _elapsed(self) -> float:
        return self._clock() - self._started

    def _burst_is_spent(self) -> bool:
        if self._blocked:
            return True
        ceiling = self._predicted_ceiling()
        return ceiling is not None and self._burst_successes >= ceiling

    def request_permitted(self) -> bool:
        """Wait out a spent burst if needed; return ``False`` when the run must end."""
        if self._stop_reason is not None:
            return False
        limit = self._policy.max_runtime_seconds
        if limit is not None and self._elapsed() >= limit:
            self._stop_reason = f"the {limit:.0f}s runtime limit was reached"
            return False
        if not self._burst_is_spent():
            return True
        if self._cooldowns_used >= self._policy.max_cooldowns:
            self._stop_reason = (
                f"the origin is still refusing this client after {self._cooldowns_used} "
                "cooldown(s), which is the configured maximum"
            )
            return False
        if limit is not None and self._elapsed() + self._policy.cooldown_seconds >= limit:
            self._stop_reason = (
                "another cooldown would run past the "
                f"{limit:.0f}s runtime limit, so the run ends here instead"
            )
            return False
        self._cooldown()
        return True

    def _cooldown(self) -> None:
        self._cooldowns_used += 1
        logger.info(
            "Pausing %.0fs for cooldown %d/%d after %d request(s) in this burst. The block is "
            "per source IP and self-clearing, so waiting is the only thing that restores it.",
            self._policy.cooldown_seconds,
            self._cooldowns_used,
            self._policy.max_cooldowns,
            self._burst_successes,
        )
        self._sleep(self._policy.cooldown_seconds)
        self._burst_successes = 0
        self._blocked = False

    def record_success(self) -> None:
        self._burst_successes += 1

    def record_block(self) -> None:
        """End the current burst and remember how far it got.

        Every further request while blocked is refused *and* renews the TTL, so the burst
        stops here rather than firing the rest of its queue into the block.
        """
        self._blocked = True
        ceiling = self._burst_successes
        if self._observed_ceiling is None or ceiling < self._observed_ceiling:
            self._observed_ceiling = ceiling
        logger.warning(
            "Origin refused this client after %d successful request(s) in the burst. Stopping "
            "the burst; the next one will aim for %s request(s).",
            self._burst_successes,
            self._predicted_ceiling(),
        )


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_COOLDOWNS",
    "BudgetPolicy",
    "RequestBudget",
]
