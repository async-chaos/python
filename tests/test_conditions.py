from __future__ import annotations

import threading

import pytest

from asynchaos.conditions import ProbabilityCondition, RateCondition, coerce_condition


# ---------------------------------------------------------------------------
# ProbabilityCondition
# ---------------------------------------------------------------------------


def test_probability_always():
    c = ProbabilityCondition(1.0)
    assert all(c.should_trigger() for _ in range(100))


def test_probability_never():
    c = ProbabilityCondition(0.0)
    assert not any(c.should_trigger() for _ in range(100))


def test_probability_statistical():
    c = ProbabilityCondition(0.3)
    count = sum(c.should_trigger() for _ in range(10_000))
    assert 2500 < count < 3500, f"Expected ~3000 triggers, got {count}"


def test_probability_invalid_below():
    with pytest.raises(ValueError):
        ProbabilityCondition(-0.1)


def test_probability_invalid_above():
    with pytest.raises(ValueError):
        ProbabilityCondition(1.1)


def test_probability_boundary_zero():
    c = ProbabilityCondition(0.0)
    assert c._p == 0.0
    assert c.should_trigger() is False


def test_probability_boundary_one():
    c = ProbabilityCondition(1.0)
    assert c._p == 1.0
    assert c.should_trigger() is True


# ---------------------------------------------------------------------------
# RateCondition
# ---------------------------------------------------------------------------


def test_rate_condition_pattern():
    rc = RateCondition(fail_count=2, window=5)
    results = [rc.should_trigger() for _ in range(10)]
    assert results == [True, True, False, False, False, True, True, False, False, False]


def test_rate_condition_full_window():
    rc = RateCondition(fail_count=5, window=5)
    assert all(rc.should_trigger() for _ in range(10))


def test_rate_condition_zero_failures():
    rc = RateCondition(fail_count=0, window=5)
    assert not any(rc.should_trigger() for _ in range(10))


def test_rate_condition_single_call():
    rc = RateCondition(fail_count=1, window=1)
    assert all(rc.should_trigger() for _ in range(5))


def test_rate_condition_thread_safe():
    rc = RateCondition(fail_count=50, window=100)
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        r = rc.should_trigger()
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 50, f"Expected exactly 50 triggers, got {sum(results)}"


def test_rate_condition_invalid_fail_exceeds_window():
    with pytest.raises(ValueError):
        RateCondition(fail_count=6, window=5)


def test_rate_condition_invalid_negative_fail():
    with pytest.raises(ValueError):
        RateCondition(fail_count=-1, window=5)


def test_rate_condition_invalid_zero_window():
    with pytest.raises(ValueError):
        RateCondition(fail_count=1, window=0)


# ---------------------------------------------------------------------------
# coerce_condition
# ---------------------------------------------------------------------------


def test_coerce_float():
    c = coerce_condition(0.5)
    assert isinstance(c, ProbabilityCondition)
    assert c._p == 0.5


def test_coerce_int():
    c = coerce_condition(1)
    assert isinstance(c, ProbabilityCondition)
    assert c._p == 1.0


def test_coerce_condition_passthrough():
    rc = RateCondition(1, 2)
    assert coerce_condition(rc) is rc


def test_coerce_invalid_string():
    with pytest.raises(TypeError):
        coerce_condition("0.5")  # type: ignore[arg-type]


def test_coerce_invalid_none():
    with pytest.raises(TypeError):
        coerce_condition(None)  # type: ignore[arg-type]
