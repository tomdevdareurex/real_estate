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

    # Each burst serves one page before being refused, so the run is being rationed rather
    # than refused outright and it is the cooldown allowance that ends it.
    for _ in range(3):
        budget.record_success()
        budget.record_block()
        if budget.cooldowns_used < 2:
            assert budget.request_permitted()

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
def test_consecutive_empty_bursts_end_the_run_before_the_cooldown_allowance() -> None:
    # A burst that serves nothing means the block did not lapse while we waited. The learned
    # ceiling cannot see this - an empty burst drives it to zero and the minimum_burst floor
    # lifts it straight back up - so without this guard the run buys every remaining cooldown
    # to rediscover that it is still blocked.
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=1500.0, max_cooldowns=4, max_empty_bursts=2)

    budget.record_block()
    assert budget.request_permitted()
    budget.record_block()

    assert not budget.request_permitted()
    assert budget.cooldowns_used == 1
    assert clock.slept == [1500.0]
    assert budget.stop_reason is not None
    assert "served nothing" in budget.stop_reason


@pytest.mark.unit
def test_a_productive_burst_resets_the_empty_burst_counter() -> None:
    # The guard fires on consecutive emptiness, not cumulative: a burst that served pages
    # proves the block does lapse, so earlier empty ones say nothing about the current one.
    clock = _FakeClock()
    budget = _budget(
        clock, cooldown_seconds=1.0, max_cooldowns=9, max_empty_bursts=2, minimum_burst=1
    )

    budget.record_block()
    assert budget.request_permitted()

    budget.record_success()
    budget.record_block()
    assert budget.request_permitted()

    budget.record_block()
    assert budget.request_permitted()

    assert budget.cooldowns_used == 3
    assert budget.stop_reason is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "policy",
    [
        {"cooldown_seconds": -1.0},
        {"max_cooldowns": -1},
        {"max_runtime_seconds": 0.0},
        {"safety_margin": -1},
        {"minimum_burst": 0},
        {"max_empty_bursts": 0},
    ],
)
def test_invalid_policies_are_rejected(policy: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        BudgetPolicy(**policy)  # type: ignore[arg-type]


def _renewing_budget(clock: _FakeClock, renewer: object, **policy: object) -> RequestBudget:
    return RequestBudget(
        BudgetPolicy(**policy),  # type: ignore[arg-type]
        sleeper=clock.sleep,
        clock=clock,
        renewer=renewer,  # type: ignore[arg-type]
    )


def _spend_burst(budget: RequestBudget, successes: int) -> None:
    """Serve `successes` requests, then have the origin refuse the next one."""
    for _ in range(successes):
        assert budget.request_permitted()
        budget.record_success()
    budget.record_block()


@pytest.mark.unit
def test_a_renewed_session_clears_the_block_without_waiting() -> None:
    """Re-earning the session removes the block, so there is nothing left to wait out."""
    clock = _FakeClock()
    budget = _renewing_budget(clock, lambda: True)

    _spend_burst(budget, 6)

    assert budget.request_permitted()
    assert clock.slept == []
    assert budget.cooldowns_used == 0
    assert budget.sessions_renewed == 1


@pytest.mark.unit
def test_a_renewal_forgets_the_ceiling_the_replaced_session_taught() -> None:
    """A ceiling measured on a spent cookie would cripple the one that replaced it.

    An unsolved-challenge session is spent after ~6 requests and a solved one runs past 100,
    so carrying the old observation across would cap the new session at `minimum_burst` and
    put the run straight back into the cooldowns the renewal just bought its way out of.
    """
    clock = _FakeClock()
    budget = _renewing_budget(clock, lambda: True)

    _spend_burst(budget, 6)
    assert budget.observed_ceiling == 6

    assert budget.request_permitted()
    assert budget.observed_ceiling is None

    for _ in range(50):
        assert budget.request_permitted()
        budget.record_success()
    assert clock.slept == []


@pytest.mark.unit
def test_a_declined_renewal_falls_back_to_waiting_out_the_block() -> None:
    """A renewal that cannot happen must not end a run that could still finish slowly."""
    clock = _FakeClock()
    budget = _renewing_budget(clock, lambda: False, cooldown_seconds=1500.0)

    _spend_burst(budget, 6)

    assert budget.request_permitted()
    assert clock.slept == [1500.0]
    assert budget.cooldowns_used == 1
    assert budget.sessions_renewed == 0


@pytest.mark.unit
def test_a_run_that_can_renew_is_not_stopped_by_the_cooldown_allowance() -> None:
    """The cooldown limits bound *waiting*; a renewal costs none, so they must not apply."""
    clock = _FakeClock()
    budget = _renewing_budget(clock, lambda: True, max_cooldowns=0)

    _spend_burst(budget, 6)

    assert budget.request_permitted()
    assert budget.stop_reason is None
    assert clock.slept == []


@pytest.mark.unit
def test_renewals_are_bounded_so_a_renewer_that_always_claims_success_cannot_spin() -> None:
    """Nothing else bounds renewal: it consumes no cooldown and no wall clock."""
    clock = _FakeClock()
    attempts: list[int] = []

    def always_succeeds() -> bool:
        attempts.append(1)
        return True

    budget = _renewing_budget(
        clock, always_succeeds, max_session_renewals=2, cooldown_seconds=1500.0
    )

    for _ in range(3):
        _spend_burst(budget, 6)
        assert budget.request_permitted()

    assert len(attempts) == 2
    assert budget.sessions_renewed == 2
    # The third block found the renewal allowance spent and waited instead.
    assert clock.slept == [1500.0]


@pytest.mark.unit
def test_a_budget_without_a_renewer_behaves_exactly_as_before() -> None:
    """Unattended runs get the wait-only path; nothing about them may change."""
    clock = _FakeClock()
    budget = _budget(clock, cooldown_seconds=1500.0)

    _spend_burst(budget, 6)

    assert budget.request_permitted()
    assert clock.slept == [1500.0]
    assert budget.cooldowns_used == 1
    assert budget.sessions_renewed == 0
