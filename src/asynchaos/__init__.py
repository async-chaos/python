from __future__ import annotations

from .conditions import Condition, ProbabilityCondition, RateCondition
from .context import _global_config
from .decorators import inject_latency
from .exceptions import ChaosException, ChaosTimeout, ConnectionDropped, LatencyInjected


def enable() -> None:
    """Re-enable chaos injection globally (default state)."""
    _global_config.enable()


def disable() -> None:
    """Disable all chaos injection globally. All decorators and zones become no-ops."""
    _global_config.disable()


def configure(*, global_probability: float = 1.0) -> None:
    """Configure library-wide settings.

    global_probability: scales ALL condition probabilities multiplicatively.
        0.0 — effectively disabled (same as disable())
        0.5 — all chaos fires at half the configured rate
        1.0 — default; configured probabilities apply unmodified
    """
    _global_config.configure(global_probability=global_probability)


__version__ = "0.1.0"

__all__ = [
    # decorators
    "inject_latency",
    # global control
    "enable",
    "disable",
    "configure",
    # conditions
    "Condition",
    "ProbabilityCondition",
    "RateCondition",
    # exceptions
    "ChaosException",
    "ConnectionDropped",
    "ChaosTimeout",
    "LatencyInjected",
]
