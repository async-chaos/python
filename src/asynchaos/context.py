from __future__ import annotations

import contextvars
import threading
from typing import Optional


# ---------------------------------------------------------------------------
# Global configuration singleton
# ---------------------------------------------------------------------------

class _GlobalConfig:
    """Thread-safe process-global library configuration.

    Uses threading.RLock (not asyncio.Lock) because:
    - asyncio.Lock requires a running event loop
    - Test setUp/tearDown methods are often synchronous
    - Third-party event loops (uvloop) may run tasks on multiple OS threads

    RLock (reentrant) rather than Lock so the same thread can call
    enable() inside configure() without deadlocking.
    """

    def __init__(self) -> None:
        self._enabled: bool = True
        self._global_probability: float = 1.0
        self._lock = threading.RLock()

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def configure(self, *, global_probability: Optional[float] = None) -> None:
        with self._lock:
            if global_probability is not None:
                if not (0.0 <= global_probability <= 1.0):
                    raise ValueError(
                        f"global_probability must be in [0.0, 1.0], "
                        f"got {global_probability!r}"
                    )
                self._global_probability = global_probability

    @property
    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def global_probability(self) -> float:
        with self._lock:
            return self._global_probability


# Module-level singleton shared across all imports via Python's module cache.
_global_config = _GlobalConfig()


# ---------------------------------------------------------------------------
# ContextVar for scoped chaos zones (populated in commit 7)
# ---------------------------------------------------------------------------

class _ZoneConfig:
    """Immutable snapshot of chaos_zone parameters stored in the ContextVar.

    Using __slots__ to prevent accidental mutation after creation — callers
    should create a new _ZoneConfig rather than modifying an existing one.
    """

    __slots__ = (
        "latency_min_ms",
        "latency_max_ms",
        "drop_condition",
        "drop_exception",
        "timeout_seconds",
    )

    def __init__(
        self,
        *,
        latency_min_ms: Optional[float] = None,
        latency_max_ms: Optional[float] = None,
        drop_condition: Optional[object] = None,
        drop_exception: type = ConnectionError,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.latency_min_ms = latency_min_ms
        self.latency_max_ms = latency_max_ms
        self.drop_condition = drop_condition
        self.drop_exception = drop_exception
        self.timeout_seconds = timeout_seconds


# THE single ContextVar for the entire library.
# Default is None (no zone active).
# asyncio.create_task() snapshots the current context at creation time,
# so tasks created inside a chaos_zone inherit the zone config automatically.
_CHAOS_ZONE_VAR: contextvars.ContextVar[Optional[_ZoneConfig]] = contextvars.ContextVar(
    "asynchaos_zone", default=None
)
