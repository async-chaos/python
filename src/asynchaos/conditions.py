from __future__ import annotations

import random
import threading
from typing import Union


class Condition:
    """Abstract trigger predicate. Subclass and implement should_trigger()."""

    def should_trigger(self) -> bool:
        raise NotImplementedError


class ProbabilityCondition(Condition):
    """Fires randomly with the given probability in [0.0, 1.0].

    Edge cases are handled explicitly to avoid float precision issues:
    - probability == 0.0: always False (RNG never called)
    - probability >= 1.0: always True  (RNG never called)
    """

    __slots__ = ("_p",)

    def __init__(self, probability: float) -> None:
        if not (0.0 <= probability <= 1.0):
            raise ValueError(
                f"probability must be in [0.0, 1.0], got {probability!r}"
            )
        self._p = float(probability)

    def should_trigger(self) -> bool:
        if self._p == 0.0:
            return False
        if self._p >= 1.0:
            return True
        return random.random() < self._p


class RateCondition(Condition):
    """Fires on the first `fail_count` calls within each rolling window of `window` calls.

    Example: RateCondition(fail_count=2, window=5) fires on calls 1, 2, 6, 7, 11, 12, ...

    Thread-safe via threading.Lock — asyncio tasks may run across OS threads
    when using third-party event loop implementations (e.g. uvloop workers).
    """

    __slots__ = ("_fail_count", "_window", "_n", "_lock")

    def __init__(self, fail_count: int, window: int) -> None:
        if window <= 0 or fail_count < 0 or fail_count > window:
            raise ValueError(
                f"Require 0 <= fail_count <= window > 0; "
                f"got fail_count={fail_count}, window={window}"
            )
        self._fail_count = fail_count
        self._window = window
        self._n = 0
        self._lock = threading.Lock()

    def should_trigger(self) -> bool:
        with self._lock:
            self._n += 1
            return ((self._n - 1) % self._window) < self._fail_count


def coerce_condition(val: Union[float, int, "Condition"]) -> Condition:
    """Convert a float/int probability to ProbabilityCondition; pass Condition through."""
    if isinstance(val, Condition):
        return val
    if isinstance(val, (int, float)):
        return ProbabilityCondition(float(val))
    raise TypeError(
        f"Expected a float probability or Condition instance, got {type(val).__name__!r}"
    )
