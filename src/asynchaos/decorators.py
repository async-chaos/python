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
