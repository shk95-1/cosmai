"""A session that takes the default registrations away and does not put them back -- the #30 shape.

Not named `test_*.py`, so nothing collects it by walking the tree; `tests/test_conftest_guard.py`
hands it to pytest by path, which is the one way a file outside the naming pattern is collected. It
exists because the session guard in conftest.py can only be shown to still fire by a session that
really breaks the registry, and a file that broke the registry inside the suite would break the
suite (tests/test_no_orphaned_test_files.py names it for the same reason).
"""

from __future__ import annotations

from analysis import registry


def test_this_session_leaves_the_registry_empty():
    for task in registry.TASKS:
        registry.unregister(task)
