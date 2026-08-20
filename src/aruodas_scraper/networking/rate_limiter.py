"""Configurable delay policy for network requests."""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

# Exposed as a module constant because `DelayPolicy` uses slots: dataclasses strip the
# class-level defaults when building `__slots__`, so `DelayPolicy.minimum_seconds` is a
# member descriptor rather than a number. Callers that need the default without building an
# instance - the CLI reads it as an option default - must use this instead.
DEFAULT_MINIMUM_DELAY_SECONDS = 10.0

# Half-width of the symmetric jitter band used by `centred`, for the same reason.
DEFAULT_JITTER_SECONDS = 2.0

# Roughly one request in eight is preceded by a reading pause. Frequent enough to break up a
# run of a few dozen requests, rare enough that it does not dominate the runtime.
DEFAULT_PAUSE_PROBABILITY = 0.125


@dataclass(frozen=True, slots=True)
class DelayPolicy:
    """Minimum delay plus bounded random jitter, and occasional long reading pauses."""

    minimum_seconds: float = DEFAULT_MINIMUM_DELAY_SECONDS
    random_min_seconds: float = 2.0
    random_max_seconds: float = 5.0
    # A person browsing listings does not request pages on a metronome: most gaps are short
    # and a few are far longer, because they stopped to read one. Jitter alone cannot produce
    # that shape, since it is bounded by the band it is drawn from. Off by default so that a
    # policy built directly - as the tests and library callers do - stays predictable.
    pause_probability: float = 0.0
    pause_min_seconds: float = 30.0
    pause_max_seconds: float = 90.0

    @classmethod
    def centred(
        cls,
        center_seconds: float,
        jitter_seconds: float = DEFAULT_JITTER_SECONDS,
        pause_probability: float = 0.0,
    ) -> "DelayPolicy":
        """Return a policy whose delay is ``center_seconds`` give or take ``jitter_seconds``.

        `wait` is additive - it draws jitter from ``[random_min, random_max]`` and adds it to
        ``minimum_seconds`` - and `validate` forbids negative bounds, so a symmetric band
        cannot be stated directly. It is encoded instead as a floor of ``center - jitter``
        plus a draw from ``[0, 2 * jitter]``, which yields the same interval and the same
        mean while keeping every stored value non-negative.

        Args:
            center_seconds: Mean gap between requests.
            jitter_seconds: Half-width of the randomised band around the mean.
            pause_probability: Chance that a delay also carries a long reading pause.

        Returns:
            Policy delaying within ``[center - jitter, center + jitter]``, plus a reading
            pause on that fraction of calls.

        Raises:
            ValueError: If either argument is negative, or the band would reach below zero.
        """
        if center_seconds < 0 or jitter_seconds < 0:
            raise ValueError("Delay values cannot be negative.")
        if jitter_seconds > center_seconds:
            raise ValueError(
                "jitter_seconds cannot exceed center_seconds: "
                f"{jitter_seconds} would put the minimum delay below zero."
            )
        return cls(
            minimum_seconds=center_seconds - jitter_seconds,
            random_min_seconds=0.0,
            random_max_seconds=2 * jitter_seconds,
            pause_probability=pause_probability,
        )

    def validate(self) -> None:
        """Raise when the configured delay range is invalid."""
        if self.minimum_seconds < 0 or self.random_min_seconds < 0:
            raise ValueError("Delay values cannot be negative.")
        if self.random_max_seconds < self.random_min_seconds:
            raise ValueError("Maximum jitter cannot be less than minimum jitter.")
        if not 0.0 <= self.pause_probability <= 1.0:
            raise ValueError("pause_probability must be between zero and one.")
        if self.pause_min_seconds < 0:
            raise ValueError("Delay values cannot be negative.")
        if self.pause_max_seconds < self.pause_min_seconds:
            raise ValueError("pause_max_seconds cannot be less than pause_min_seconds.")

    def wait(self, sleeper: Callable[[float], None] = time.sleep) -> float:
        """Wait according to policy and return the selected duration."""
        self.validate()
        rng = secrets.SystemRandom()
        duration = self.minimum_seconds + rng.uniform(
            self.random_min_seconds, self.random_max_seconds
        )
        if self.pause_probability and rng.random() < self.pause_probability:
            duration += rng.uniform(self.pause_min_seconds, self.pause_max_seconds)
        sleeper(duration)
        return duration
