"""#216: class A runs across processes, and the deploy sites are what cannot join them.

`db/migrate.sh` drops and recreates every view in `needs` (db/migrate.sh, stage f). Run beside three
other pytest processes, that takes out views a test on another worker is reading -- and the deploy
itself cannot even start, because `tests/conftest.py`'s `deploy` fixture waits for every
needs_migrator connection to be free and the other workers hold theirs for as long as they run.

So the group is not "same worker" (`--dist loadgroup` would give that and no more) but "alone":
`tool/checks/test` runs the parallel workers first and then a second, single-process pytest over the
`serial` marker. This file is the rule that keeps the two in step -- a new test file that deploys
must carry the marker, or it silently rejoins the parallel run.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
CHECK = REPO_ROOT / "tool" / "checks" / "test"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The three files #228's option 6 counted: 12 tests that run a whole deploy mid-suite.
KNOWN_DEPLOY_SITES = (
    "tests/test_empty_db_bootstrap.py",
    "tests/test_migrate_view_sweep.py",
    "tests/test_secret_file_rule.py",
)
# How a test asks for the one fixture that runs db/migrate.sh (tests/conftest.py `deploy`).
DEPLOYS = re.compile(r"\bdeploy\s*:\s*Callable")
# The other database-wide name: the commerce source lock. `analysis/locks.py`'s analyze key gets a
# namespace per worker in tests/conftest.py, but this one cannot -- the file below proves the key is
# the same number in every process, which is the property a rename would destroy. So the tests that
# take it run alone instead.
TAKES_A_SOURCE_LOCK = re.compile(r"\bcli\.run\(|PostgresSourceLock")
KNOWN_SOURCE_LOCK_SITES = (
    "tests/collectors/commerce/test_cli_uses_a_live_transport.py",
    "tests/collectors/commerce/test_source_lock.py",
    "tests/collectors/commerce/test_sources_walk_in_parallel_lanes.py",
)
MARKED_SERIAL = re.compile(r"pytestmark\s*=.*\bpytest\.mark\.serial\b|@pytest\.mark\.serial\b", re.DOTALL)


def _sites(pattern: re.Pattern[str]) -> list[str]:
    return [
        str(path.relative_to(REPO_ROOT))
        for path in sorted(TESTS_DIR.rglob("test_*.py"))
        # This file quotes both detectors, so it matches them; it takes no lock and deploys nothing.
        if path != Path(__file__) and pattern.search(path.read_text(encoding="utf-8"))
    ]


def _deploy_sites() -> list[str]:
    return _sites(DEPLOYS)


def test_the_detector_still_finds_the_three_known_deploy_sites():
    # Without this, a renamed fixture would make the rule below vacuous instead of failing.
    assert set(KNOWN_DEPLOY_SITES) <= set(_deploy_sites()), _deploy_sites()


def test_every_file_that_runs_a_deploy_is_in_the_serial_group():
    unmarked = [
        site for site in _deploy_sites() if not MARKED_SERIAL.search((REPO_ROOT / site).read_text("utf-8"))
    ]
    assert not unmarked, (
        "these run db/migrate.sh mid-suite but would be handed to an xdist worker: "
        f"mark them `pytest.mark.serial` -- {unmarked}"
    )


def test_the_detector_still_finds_the_known_source_lock_sites():
    assert sorted(_sites(TAKES_A_SOURCE_LOCK)) == sorted(KNOWN_SOURCE_LOCK_SITES), _sites(TAKES_A_SOURCE_LOCK)


def test_every_file_that_takes_a_source_lock_is_in_the_serial_group():
    """Measured 2026-09-06: at -n 4 without this, eight tests of test_source_lock.py failed on
    `assert held` -- two workers inside one file, both taking `oliveyoung`."""
    unmarked = [
        site
        for site in _sites(TAKES_A_SOURCE_LOCK)
        if not MARKED_SERIAL.search((REPO_ROOT / site).read_text("utf-8"))
    ]
    assert not unmarked, f"these take a database-wide advisory key on an xdist worker: {unmarked}"


def test_the_serial_marker_is_registered():
    assert "serial:" in PYPROJECT.read_text(encoding="utf-8"), "an unregistered marker is --strict-markers"


def test_the_dev_extra_carries_the_parallel_runner():
    assert "pytest-xdist" in PYPROJECT.read_text(encoding="utf-8")


def test_the_parallel_invocation_leaves_the_serial_group_out():
    body = CHECK.read_text(encoding="utf-8")
    assert "-n " in body, body
    assert "not serial" in body, "the parallel run must deselect the serial group"


def test_the_serial_group_gets_a_run_of_its_own_with_no_workers():
    """A second invocation, not `--dist loadgroup`: a group pinned to one worker still runs while the
    other three do, which is the thing the deploy cannot survive."""
    body = CHECK.read_text(encoding="utf-8")
    serial_line = [ln for ln in body.splitlines() if "and serial" in ln]
    assert serial_line, body
    assert all("-n " not in ln for ln in serial_line), serial_line
