from __future__ import annotations

import pytest
import asynchaos


@pytest.fixture(autouse=True)
def reset_asynchaos():
    """Restore asynchaos global state after every test.

    Prevents a test that calls asynchaos.disable() or asynchaos.configure()
    from contaminating subsequent tests in the session.
    """
    asynchaos.enable()
    asynchaos.configure(global_probability=1.0)
    yield
    asynchaos.enable()
    asynchaos.configure(global_probability=1.0)
