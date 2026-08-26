"""Guards against a repeat of #8 수정 라운드 2's finding: three files under tests/ carried `def test_`
functions but a basename (`youtube_test_pg_load.py` and two siblings) pytest's default collection
(`test_*.py` / `*_test.py`) never matched -- `tool/checks/test` stayed green for two review rounds
while those files' assertions, including a DDL completion-bar check, silently never ran. This makes
that failure shape mechanical rather than something a reviewer has to notice by eye.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Files under tests/ that deliberately hold no tests of their own and are not named test_*.py --
# named here rather than guessed at by the scan, so nobody can widen this list without a reason a
# reviewer can see in the diff.
ALLOWED_NON_TEST_MODULES = frozenset(
    {
        # A fixture module analysis.registry's load_implementations() calls register_implementations()
        # on (#2/#3/#4 shape) -- imported by other tests, not collected as one itself, holds no `def test_`.
        "fake_implementation.py",
    }
)

_TEST_FUNCTION = re.compile(r"^def test_", re.MULTILINE)
_TEST_CLASS = re.compile(r"^class Test", re.MULTILINE)


def _is_collectible_name(name: str) -> bool:
    return name in ("__init__.py", "conftest.py") or name.startswith("test_") or name.endswith("_test.py")


def test_no_test_file_sits_outside_pytests_collection_pattern():
    orphans = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        name = path.name
        if _is_collectible_name(name) or name in ALLOWED_NON_TEST_MODULES:
            continue
        text = path.read_text(encoding="utf-8")
        if _TEST_FUNCTION.search(text) or _TEST_CLASS.search(text):
            orphans.append(str(path.relative_to(TESTS_DIR.parent)))
    assert not orphans, (
        "these hold tests but pytest's default collection (test_*.py / *_test.py) will never find "
        f"them -- rename to that pattern, or add to ALLOWED_NON_TEST_MODULES with a reason: {orphans}"
    )
