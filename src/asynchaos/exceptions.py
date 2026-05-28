from __future__ import annotations


class ChaosException(Exception):
    """Base class for all asynchaos-injected exceptions."""


class ConnectionDropped(ChaosException, ConnectionError):
    """Raised by @drop_connections / chaos_zone when a connection is simulated as dropped.

    Inherits from both ChaosException and ConnectionError so existing handlers
    that catch ConnectionError continue to work without modification.
    """


class ChaosTimeout(ChaosException, TimeoutError):
    """Raised by @timeout / @chaos when the timeout budget is exceeded.

    Inherits from TimeoutError so it's catchable by standard handlers.
    Note: in Python 3.11+ asyncio.TimeoutError IS TimeoutError; this class
    provides a distinct type for test assertions.
    """


class LatencyInjected(ChaosException):
    """Optionally raised instead of sleeping when configured in exception mode.

    Normally @inject_latency sleeps silently. This exception is available for
    cases where raising is preferable to blocking (e.g., strict latency budgets).
    """
