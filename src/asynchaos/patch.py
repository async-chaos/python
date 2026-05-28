from __future__ import annotations

import asyncio
import contextlib
import functools
import random
from typing import Iterable, Optional, Type

from .context import _CHAOS_ZONE_VAR, _global_config


class _RestorationHandle:
    """Tracks a single patched method so it can be cleanly restored."""

    __slots__ = ("_obj", "_name", "_original")

    def __init__(self, obj: type, name: str, original: object) -> None:
        self._obj = obj
        self._name = name
        self._original = original

    def restore(self) -> None:
        setattr(self._obj, self._name, self._original)


@contextlib.contextmanager
def chaos_patch(
    client_class: type,
    methods: Iterable[str],
    *,
    latency_min_ms: Optional[float] = None,
    latency_max_ms: Optional[float] = None,
    latency: Optional[float] = None,
    drop_rate: Optional[float] = None,
    drop_exception: Type[Exception] = ConnectionError,
):
    """Sync context manager that monkey-patches async methods on a class.

    Designed for use in pytest fixtures or test setUp/tearDown. Patches are
    applied to the CLASS (not an instance), so all instances — created before
    or after the patch — are affected while the context is active.

    The patch reads the active chaos_zone and _global_config at CALL TIME,
    so it composes correctly with chaos_zone and asynchaos.disable().

    Originals are always restored in the finally block, even if the body raises.

    Usage:
        with chaos_patch(aiohttp.ClientSession, ["get", "post"],
                         latency_min_ms=200, drop_rate=0.3):
            response = await session.get(url)   # chaotic

        response = await session.get(url)       # restored, normal

    Args:
        client_class: The class whose methods will be patched.
        methods: Iterable of method names to patch.
        latency_min_ms: Minimum injected delay in ms.
        latency_max_ms: Maximum injected delay in ms.
        latency: Shorthand that sets both min and max to the same value.
        drop_rate: Float probability of dropping the connection.
        drop_exception: Exception raised on drop (default ConnectionError).
    """
    if latency is not None:
        latency_min_ms = latency_max_ms = latency

    handles: list[_RestorationHandle] = []
    try:
        for method_name in methods:
            original = getattr(client_class, method_name)
            if not asyncio.iscoroutinefunction(original):
                raise TypeError(
                    f"{client_class.__name__}.{method_name} is not a coroutine function; "
                    f"chaos_patch only supports async methods."
                )

            # Close over original with a default argument to avoid late-binding.
            @functools.wraps(original)
            async def patched(self_or_cls, *args, _orig=original, **kwargs):
                if _global_config.is_enabled:
                    zone = _CHAOS_ZONE_VAR.get()

                    eff_min = (
                        zone.latency_min_ms
                        if (zone and zone.latency_min_ms is not None)
                        else latency_min_ms
                    )
                    eff_max = (
                        zone.latency_max_ms
                        if (zone and zone.latency_max_ms is not None)
                        else latency_max_ms
                    )

                    if eff_min is not None and eff_max is not None:
                        await asyncio.sleep(random.uniform(eff_min, eff_max) / 1000.0)

                    eff_drop = drop_rate
                    if zone and zone.drop_condition is not None:
                        if zone.drop_condition.should_trigger():
                            raise zone.drop_exception(
                                f"Connection dropped by asynchaos chaos_patch via zone"
                            )
                        eff_drop = None  # zone already handled drop

                    if eff_drop is not None and random.random() < eff_drop:
                        raise drop_exception(
                            f"Connection dropped by asynchaos chaos_patch on "
                            f"{client_class.__name__}.{_orig.__name__!r}"
                        )

                return await _orig(self_or_cls, *args, **kwargs)

            setattr(client_class, method_name, patched)
            handles.append(_RestorationHandle(client_class, method_name, original))

        yield

    finally:
        for handle in handles:
            handle.restore()
