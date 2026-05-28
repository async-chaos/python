from __future__ import annotations

import asyncio
import functools
import inspect
import random
from typing import Optional, Type, Union

from .conditions import Condition, ProbabilityCondition, coerce_condition
from .context import _CHAOS_ZONE_VAR, _global_config


def _require_async(fn: object, decorator_name: str) -> None:
    """Validate at decoration time so the TypeError appears at the @decorator line."""
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(
            f"@{decorator_name} requires an async function (async def), "
            f"but {getattr(fn, '__qualname__', fn)!r} is a regular function. "
            f"Wrap sync functions with asyncio.to_thread() first."
        )


def chaos(
    *,
    latency_min_ms: Optional[float] = None,
    latency_max_ms: Optional[float] = None,
    latency: Optional[float] = None,
    drop_rate: Optional[Union[float, Condition]] = None,
    drop_exception: Type[Exception] = ConnectionError,
    timeout_seconds: Optional[float] = None,
    timeout_exception: Type[Exception] = asyncio.TimeoutError,
):
    """Combined decorator applying latency + drop + timeout in a single wrapper.

    Effect ordering (outermost → innermost):
        asyncio.wait_for(timeout_seconds)    ← fires even during the latency sleep
          latency sleep                      ← counts against the timeout budget
            drop check                       ← fires before the function body
              fn(*args, **kwargs)

    Example: chaos(latency=300, timeout_seconds=0.2) will always timeout because
    the 300ms sleep exhausts the 200ms budget — this correctly models a slow network.

    Args:
        latency_min_ms: Minimum injected delay in ms.
        latency_max_ms: Maximum injected delay in ms.
        latency: Shorthand that sets both min and max to the same value.
        drop_rate: Float probability or Condition for connection drops.
        drop_exception: Exception raised on drop (default ConnectionError).
        timeout_seconds: Hard deadline in seconds (default: no timeout).
        timeout_exception: Exception raised on timeout (default asyncio.TimeoutError).
    """
    if latency is not None:
        latency_min_ms = latency_max_ms = latency
    if latency_min_ms is not None and latency_max_ms is None:
        latency_max_ms = latency_min_ms
    if latency_max_ms is not None and latency_min_ms is None:
        latency_min_ms = latency_max_ms

    drop_condition = coerce_condition(drop_rate) if drop_rate is not None else None

    def decorator(fn):
        _require_async(fn, "chaos")

        @functools.wraps(fn)
        async def _inner(*args, **kwargs):
            if _global_config.is_enabled:
                zone = _CHAOS_ZONE_VAR.get()
                gp = _global_config.global_probability

                if latency_min_ms is not None:
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
                    await asyncio.sleep(random.uniform(eff_min, eff_max) / 1000.0)

                if drop_condition is not None:
                    eff_cond = (
                        ProbabilityCondition(drop_condition._p * gp)
                        if isinstance(drop_condition, ProbabilityCondition)
                        else drop_condition
                    )
                    if eff_cond.should_trigger():
                        raise drop_exception(
                            f"Connection dropped by asynchaos @chaos on {fn.__qualname__!r}"
                        )

            return await fn(*args, **kwargs)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if timeout_seconds is not None and _global_config.is_enabled:
                if timeout_exception is asyncio.TimeoutError:
                    return await asyncio.wait_for(
                        _inner(*args, **kwargs), timeout=timeout_seconds
                    )
                else:
                    try:
                        return await asyncio.wait_for(
                            _inner(*args, **kwargs), timeout=timeout_seconds
                        )
                    except asyncio.TimeoutError as exc:
                        raise timeout_exception(str(exc)) from exc
            else:
                return await _inner(*args, **kwargs)

        return wrapper

    return decorator


def timeout(
    seconds: float,
    exception: Type[Exception] = asyncio.TimeoutError,
):
    """Decorator: wraps the function with asyncio.wait_for(timeout=seconds).

    asyncio.wait_for cancels the inner task via Task.cancel() when the deadline
    fires, and guarantees the coroutine is no longer running when it returns.

    Global disable (asynchaos.disable()) bypasses this decorator entirely.
    Unlike @inject_latency, timeout is not influenced by chaos_zone — it is
    infrastructure config, not randomized fault injection.

    Args:
        seconds: Timeout budget in seconds. Must be > 0.
        exception: Exception class to raise on timeout (default asyncio.TimeoutError).
            When overridden, the TimeoutError is caught and re-raised as this type.
    """
    if seconds <= 0:
        raise ValueError(f"timeout seconds must be > 0, got {seconds!r}")

    def decorator(fn):
        _require_async(fn, "timeout")

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not _global_config.is_enabled:
                return await fn(*args, **kwargs)

            if exception is asyncio.TimeoutError:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            else:
                try:
                    return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
                except asyncio.TimeoutError as exc:
                    raise exception(
                        f"asynchaos @timeout: {fn.__qualname__!r} exceeded {seconds}s"
                    ) from exc

        return wrapper

    return decorator


def drop_connections(
    probability: Union[float, Condition] = 0.1,
    exception: Type[Exception] = ConnectionError,
):
    """Decorator: probabilistically raises an exception before calling the function.

    The function body is never executed when the condition triggers — the
    exception propagates exactly as a real network failure would.

    The default exception is ConnectionError (not a ChaosException subclass) so
    existing error-handling code works without modification. To distinguish
    injected from real errors in tests, pass exception=asynchaos.ConnectionDropped.

    Args:
        probability: Float in [0.0, 1.0] or a Condition instance (default 0.1).
        exception: Exception class to raise (default ConnectionError).
    """
    condition = coerce_condition(probability)

    def decorator(fn):
        _require_async(fn, "drop_connections")

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if _global_config.is_enabled:
                zone = _CHAOS_ZONE_VAR.get()

                eff_condition = condition
                eff_exception = exception

                if zone and zone.drop_condition is not None:
                    eff_condition = zone.drop_condition
                    eff_exception = zone.drop_exception

                if isinstance(eff_condition, ProbabilityCondition):
                    eff_condition = ProbabilityCondition(
                        eff_condition._p * _global_config.global_probability
                    )

                if eff_condition.should_trigger():
                    raise eff_exception(
                        f"Connection dropped by asynchaos @drop_connections "
                        f"on {fn.__qualname__!r}"
                    )

            return await fn(*args, **kwargs)

        return wrapper

    return decorator


def inject_latency(
    min_ms: float = 100.0,
    max_ms: float = 500.0,
    probability: Union[float, Condition] = 1.0,
):
    """Decorator: adds a random async sleep before each call.

    The sleep uses asyncio.sleep() which yields to the event loop, so other
    tasks continue running during the injected delay — fully cooperative.

    Zone override: if an active chaos_zone specifies latency_min_ms/max_ms,
    those values replace the decorator's defaults for that call.

    Args:
        min_ms: Minimum delay in milliseconds (default 100).
        max_ms: Maximum delay in milliseconds (default 500).
        probability: Float in [0.0, 1.0] or a Condition instance.
    """
    condition = coerce_condition(probability)

    def decorator(fn):
        _require_async(fn, "inject_latency")

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if _global_config.is_enabled:
                zone = _CHAOS_ZONE_VAR.get()

                eff_min = (
                    zone.latency_min_ms
                    if (zone and zone.latency_min_ms is not None)
                    else min_ms
                )
                eff_max = (
                    zone.latency_max_ms
                    if (zone and zone.latency_max_ms is not None)
                    else max_ms
                )

                # Scale probability by global multiplier (multiplicative composition).
                # This ensures global_probability=0.0 acts identically to disable().
                if isinstance(condition, ProbabilityCondition):
                    eff_condition = ProbabilityCondition(
                        condition._p * _global_config.global_probability
                    )
                else:
                    eff_condition = condition

                if eff_condition.should_trigger():
                    delay_s = random.uniform(eff_min, eff_max) / 1000.0
                    await asyncio.sleep(delay_s)

            return await fn(*args, **kwargs)

        return wrapper

    return decorator
