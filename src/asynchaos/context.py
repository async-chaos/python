from __future__ import annotations

import asyncio
import contextvars
import random
import threading
from typing import Optional, Type


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


# ---------------------------------------------------------------------------
# chaos_zone async context manager
# ---------------------------------------------------------------------------

class chaos_zone:
    """Async context manager for scoped chaos injection.

    Sets the active _ZoneConfig in the ContextVar for the duration of the block.
    All decorators read this at call time, so any @inject_latency / @drop_connections
    / @chaos decorated function called inside the zone uses the zone's parameters
    instead of the decorator's defaults.

    Propagation: asyncio.create_task() snapshots the current context at creation
    time, so tasks CREATED inside the zone inherit the zone config even after the
    zone exits. Tasks created BEFORE entering the zone are unaffected.

    Nesting: inner zones fully override outer zones. On exit, the outer zone
    config is restored via token.reset() — guaranteed even if the body raises.

    Usage:
        async with chaos_zone(latency_min_ms=100, latency_max_ms=500,
                              drop_rate=0.1) as ctx:
            await my_service()          # affected by zone
            await ctx.inject_latency()  # manual injection for non-decorated code
    """

    def __init__(
        self,
        *,
        latency_min_ms: Optional[float] = None,
        latency_max_ms: Optional[float] = None,
        latency: Optional[float] = None,
        drop_rate: Optional[float] = None,
        drop_exception: Type[Exception] = ConnectionError,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        from .conditions import coerce_condition

        if latency is not None:
            latency_min_ms = latency_max_ms = latency

        self._config = _ZoneConfig(
            latency_min_ms=latency_min_ms,
            latency_max_ms=latency_max_ms,
            drop_condition=coerce_condition(drop_rate) if drop_rate is not None else None,
            drop_exception=drop_exception,
            timeout_seconds=timeout_seconds,
        )
        self._token: Optional[contextvars.Token] = None

    async def __aenter__(self) -> "_ChaosCtxProxy":
        self._token = _CHAOS_ZONE_VAR.set(self._config)
        return _ChaosCtxProxy(self._config)

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # CRITICAL: always reset even if the body raised an exception.
        if self._token is not None:
            _CHAOS_ZONE_VAR.reset(self._token)
        return None  # do not suppress exceptions


class _ChaosCtxProxy:
    """The `as ctx` object — exposes manual injection for non-decorated code."""

    def __init__(self, config: _ZoneConfig) -> None:
        self._config = config

    async def inject_latency(self) -> None:
        """Manually apply the zone's latency. Useful for non-decorated callsites."""
        c = self._config
        if c.latency_min_ms is not None and c.latency_max_ms is not None:
            delay = random.uniform(c.latency_min_ms, c.latency_max_ms) / 1000.0
            await asyncio.sleep(delay)

    def maybe_drop(self) -> None:
        """Manually apply the zone's drop condition. Raises on trigger."""
        c = self._config
        if c.drop_condition and c.drop_condition.should_trigger():
            raise c.drop_exception("Connection dropped by asynchaos chaos_zone")
