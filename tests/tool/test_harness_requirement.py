"""The suite tells its harness-only tests that a skip is a fault (#178 re-review 5).

`tests/conftest.py`'s `harness_container` fixture skips when the throwaway container is not there --
right for a run against an external `TEST_POSTGRES_URL`, and wrong for the full suite, where the
container is the whole point and a skip means the checks that need it left a green run without a
word. The one place that knows the container exists is the branch of `tool/checks/test` that just
started it, so that is where COSMAI_REQUIRE_HARNESS is set.

Read out of the script rather than run: starting a container to prove an `export` line is there
would cost two minutes to answer a question the file answers.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE = REPO_ROOT / "tool" / "checks" / "test"
FLAG = "COSMAI_REQUIRE_HARNESS"
# The line that hands the suite's own URLs to pytest -- the end of the container branch.
URL_EXPORT = "export TEST_POSTGRES_URL TEST_POSTGRES_RUNTIME_URL"


def _body() -> str:
    return SUITE.read_text(encoding="utf-8")


def test_the_container_branch_exports_the_harness_requirement():
    body = _body()
    assert re.search(rf"^\s*{FLAG}=1$", body, re.M), f"{FLAG} is never set"
    exports = [line for line in body.splitlines() if line.strip().startswith(URL_EXPORT)]
    assert exports, "the line that exports the test URLs moved; this check no longer measures it"
    assert all(FLAG in line for line in exports), (
        f"{FLAG} is not exported beside the test URLs, so pytest never sees it"
    )


def test_the_requirement_is_set_only_where_the_container_was_started():
    """Setting it unconditionally would turn every run against an external TEST_POSTGRES_URL into a
    failure over a container that was never supposed to be there."""
    body = _body()
    branch = body.index(
        'if [ "$change_class" != C ] && [ "$change_class" != N ] && [ -z "$TEST_POSTGRES_URL" ]'
    )
    setting = body.index(f"{FLAG}=1")
    assert setting > branch, f"{FLAG} is set outside the branch that starts the container"
