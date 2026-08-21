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

# A burst that served nothing means the block had not lapsed by the time we asked again. One
# of those is bad luck; two in a row means waiting on this cadence is not restoring service,
# and every further cooldown is 25 minutes bought for no pages. The learned ceiling cannot
# notice this on its own: an empty burst drives the observation to zero, which
# `_predicted_ceiling` then floors back up to `minimum_burst`.
DEFAULT_MAX_EMPTY_BURSTS = 2

# Renewing the session clears the block outright, so unlike a cooldown it costs nothing but
# the operator's attention. This bound exists only to stop a renewer that always claims
# success from looping; a person solving challenges will never approach it.
DEFAULT_MAX_SESSION_RENEWALS = 10


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
    # Consecutive bursts that may serve nothing before the run concludes the origin is
    # refusing it outright rather than rationing it.
    max_empty_bursts: int = DEFAULT_MAX_EMPTY_BURSTS
    # Renewals cost no waiting, so nothing else bounds them: without a ceiling a renewer that
    # keeps reporting success while the origin keeps refusing would spin forever. Generous,
    # because each real renewal needs a person to solve a challenge.
    max_session_renewals: int = DEFAULT_MAX_SESSION_RENEWALS

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
        if self.max_empty_bursts < 1:
            raise ValueError("max_empty_bursts must be at least one")
        if self.max_session_renewals < 0:
            raise ValueError("max_session_renewals cannot be negative")


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
        renewer: Callable[[], bool] | None = None,
    ) -> None:
        self._policy = policy or BudgetPolicy()
        self._sleep = sleeper
        self._clock = clock
        self._started = clock()
        # Offered a chance to re-earn the session when a burst is spent. Returns True if it
        # did, in which case there is no block left to wait out. ``None`` restores the
        # wait-only behaviour, which is what an unattended run wants.
        self._renew = renewer
        self._burst_successes = 0
        self._blocked = False
        self._observed_ceiling: int | None = None
        self._cooldowns_used = 0
        self._empty_bursts = 0
        self._sessions_renewed = 0
        self._stop_reason: str | None = None

    @property
    def cooldowns_used(self) -> int:
        return self._cooldowns_used

    @property
    def sessions_renewed(self) -> int:
        """Blocks cleared by re-earning the session instead of waiting one out."""
        return self._sessions_renewed

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
        # Re-earning the session is offered before every limit below it, because all of those
        # exist to bound *waiting* - they are the answer to "cooldowns cost 25 minutes each".
        # A renewal costs none, so a run that can renew should never be stopped by a ceiling
        # on how long it may wait, including on the pass where waiting has already failed.
        if self._try_renewal():
            return True
        # `_burst_successes` is only cleared inside `_cooldown`, so it still describes the
        # burst that just ended. A spent burst is either blocked or at its predicted ceiling,
        # and the ceiling is never below one, so zero here always means "served nothing".
        self._empty_bursts = self._empty_bursts + 1 if self._burst_successes == 0 else 0
        if self._empty_bursts >= self._policy.max_empty_bursts:
            self._stop_reason = (
                f"the origin served nothing in {self._empty_bursts} consecutive burst(s), so "
                "waiting is not restoring service and further cooldowns would only cost time"
            )
            return False
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

    def _try_renewal(self) -> bool:
        """Offer the caller a chance to re-earn the session; report whether it did."""
        if self._renew is None or self._sessions_renewed >= self._policy.max_session_renewals:
            return False
        if not self._renew():
            return False
        self._sessions_renewed += 1
        logger.info(
            "Session renewed after %d request(s) in this burst; continuing without a cooldown.",
            self._burst_successes,
        )
        self.session_renewed()
        return True

    def session_renewed(self) -> None:
        """Forget what the replaced session taught about the ceiling.

        The learned ceiling describes the budget of one specific cookie, and cookies differ
        enormously: an unsolved-challenge session is spent after about 6 requests where a
        solved one runs past 100. Carrying a ceiling of 6 across a renewal would cap the new
        session at `minimum_burst` bursts and send the run back into cooldowns it no longer
        needs - which would waste most of what the renewal just bought.

        `_empty_bursts` is deliberately *not* reset. It is the guard against renewals that
        report success while the origin keeps refusing, and clearing it here would disarm the
        one check that notices.
        """
        self._observed_ceiling = None
        self._burst_successes = 0
        self._blocked = False

    def _cooldown(self) -> None:
        self._cooldowns_used += 1
        logger.info(
            "Pausing %.0fs for cooldown %d/%d after %d request(s) in this burst. The block is "
            "self-clearing, so waiting restores service - but re-earning the session clears "
            "it now: run `mint-cookie` and solve the challenge to skip waits like this one.",
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
    "DEFAULT_MAX_EMPTY_BURSTS",
    "DEFAULT_MAX_SESSION_RENEWALS",
    "BudgetPolicy",
    "RequestBudget",
]
