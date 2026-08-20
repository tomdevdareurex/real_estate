import pytest

from aruodas_scraper.networking.budget import BudgetPolicy, RequestBudget


class _FakeClock:
    """Advances only when the injected sleeper is called, so no test really waits."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _budget(clock: _FakeClock, **policy: object) -> RequestBudget:
    return RequestBudget(
        BudgetPolicy(**policy),  # type: ignore[arg-type]
        sleeper=clock.sleep,
        clock=clock,
    )


@pytest.mark.unit
def test_requests_flow_freely_until_the_origin_first_refuses() -> None:
    clock = _FakeClock()
    budget = _budget(clock)

    for _ in range(50):
        assert budget.request_permitted()
        budget.record_success()

    assert clock.slept == []
    assert budget.observed_ceiling is None


@pytest.mark.unit
def test_a_block_ends_the_burst_and_the_next_request_waits() -> None:
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=1500.0)

    for _ in range(11):
        assert budget.request_permitted()
        budget.record_success()
    budget.record_block()

    assert budget.observed_ceiling == 11
    assert budget.request_permitted()
    assert clock.slept == [1500.0]
    assert budget.cooldowns_used == 1


@pytest.mark.unit
def test_the_next_burst_stops_short_of_the_observed_ceiling() -> None:
    # The point of the safety margin: voluntarily stopping below the ceiling means the
    # block is never tripped, so no request is wasted and the TTL never restarts.
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=100.0, safety_margin=2)

    for _ in range(11):
        budget.request_permitted()
        budget.record_success()
    budget.record_block()
    budget.request_permitted()  # cooldown 1

    permitted = 0
    while budget.request_permitted():
        if budget.cooldowns_used > 1:  # a second cooldown means this burst is over
            break
        budget.record_success()
        permitted += 1

    assert permitted == 9  # 11 observed, less the margin of 2


@pytest.mark.unit
def test_the_run_ends_once_the_cooldown_allowance_is_spent() -> None:
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=10.0, max_cooldowns=2, minimum_burst=1)

    budget.record_block()
    assert budget.request_permitted()
    budget.record_block()
    assert budget.request_permitted()
    budget.record_block()

    assert not budget.request_permitted()
    assert budget.cooldowns_used == 2
    assert budget.stop_reason is not None
    assert "cooldown(s)" in budget.stop_reason


@pytest.mark.unit
def test_a_cooldown_that_would_overrun_the_runtime_limit_is_not_started() -> None:
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=1500.0, max_runtime_seconds=600.0)

    budget.record_success()
    budget.record_block()

    assert not budget.request_permitted()
    assert clock.slept == []
    assert budget.stop_reason is not None
    assert "runtime limit" in budget.stop_reason


@pytest.mark.unit
def test_the_runtime_limit_ends_the_run_even_between_bursts() -> None:
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=300.0, max_runtime_seconds=1000.0, minimum_burst=1)

    budget.record_block()
    assert budget.request_permitted()
    clock.now = 1000.0

    assert not budget.request_permitted()
    assert budget.stop_reason is not None


@pytest.mark.unit
def test_the_ceiling_tracks_the_lowest_burst_the_origin_allowed() -> None:
    # A later, smaller ceiling means the origin tightened; aiming at the earlier, larger
    # one would trip the block on every burst from then on.
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=1.0, max_cooldowns=5)

    for _ in range(11):
        budget.request_permitted()
        budget.record_success()
    budget.record_block()
    budget.request_permitted()

    for _ in range(4):
        budget.request_permitted()
        budget.record_success()
    budget.record_block()

    assert budget.observed_ceiling == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    "policy",
    [
        {"cooldown_seconds": -1.0},
        {"max_cooldowns": -1},
        {"max_runtime_seconds": 0.0},
        {"safety_margin": -1},
        {"minimum_burst": 0},
    ],
)
def test_invalid_policies_are_rejected(policy: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        BudgetPolicy(**policy)  # type: ignore[arg-type]
