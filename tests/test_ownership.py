"""The ownership boundary between upstream and the fork, read from `contracts/ownership.md`.

#150 · #103 · #115 are the record of what happens without one: objects the fork put into production
that upstream did not know were dropped or killed a check. #192's methodology row 3 turned that into
a rule -- the fork owns 02x DDL and its own modules, never `STATE.md` or the upstream guards -- and
named an ownership file plus a test as its enforcement site. This is the test half; the "forbidden
files unchanged" half is `tool/checks/ownership`, exercised at the bottom of this file.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_MD = REPO_ROOT / "contracts" / "ownership.md"
OWNERSHIP_CHECK = REPO_ROOT / "tool" / "checks" / "ownership"
NEEDS_DDL = REPO_ROOT / "contracts" / "ddl" / "needs"

# The number at which the fork's DDL range starts (#192 D7). Below it the file belongs to upstream.
FORK_DDL_FLOOR = 20

SECTIONS = ("fork-owned", "must-not-change", "shared-surface")


def _read_section(name: str) -> list[str]:
    """One fenced block per list, info string `ownership:<name>`, one path per line.

    A fenced block rather than a bullet list: `tool/checks/ownership` is POSIX sh and has to read
    the same file, and a fence is the one shape sed and python agree on without a markdown parser.
    """
    text = OWNERSHIP_MD.read_text(encoding="utf-8")
    match = re.search(rf"^```ownership:{name}$\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
    assert match, f"contracts/ownership.md has no ```ownership:{name} block"
    lines = [line.strip() for line in match.group(1).splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def _matches(pattern: str, path: str) -> bool:
    """A directory entry covers everything under it; anything else is a glob over the whole path."""
    if pattern.endswith("/"):
        return path.startswith(pattern)
    return Path(path).match(pattern) or path == pattern


def _paths_for(pattern: str) -> list[Path]:
    if pattern.endswith("/"):
        directory = REPO_ROOT / pattern
        return [directory] if directory.is_dir() else []
    return sorted(REPO_ROOT.glob(pattern))


@pytest.fixture(scope="module")
def lists() -> dict[str, list[str]]:
    return {name: _read_section(name) for name in SECTIONS}


def test_every_section_is_present_and_non_empty(lists):
    for name in SECTIONS:
        assert lists[name], f"the ownership:{name} list is empty"


def test_every_listed_path_exists(lists):
    missing = [
        f"{name}: {pattern}" for name in SECTIONS for pattern in lists[name] if not _paths_for(pattern)
    ]
    assert not missing, f"listed in contracts/ownership.md but nothing matches in the checkout: {missing}"


def test_no_path_is_both_the_forks_and_upstreams(lists):
    # A path on both lists is a rule that cannot be applied: the fork would be told to change it and
    # not to change it in the same breath.
    both = [
        (owned, forbidden)
        for owned in lists["fork-owned"]
        for forbidden in lists["must-not-change"]
        if owned == forbidden
        or any(_matches(forbidden, str(p.relative_to(REPO_ROOT))) for p in _paths_for(owned))
    ]
    assert not both, f"these are claimed by both lists: {both}"


def test_a_shared_surface_is_never_claimed_as_fork_owned(lists):
    # The third list is the fork's outbox, not its property: a file that exists on both sides is
    # changed only with the change going upstream in the same wave.
    claimed = [
        (shared, owned)
        for shared in lists["shared-surface"]
        for owned in lists["fork-owned"]
        if _matches(owned, shared)
    ]
    assert not claimed, f"a shared surface cannot also be fork-owned: {claimed}"


def test_needs_ddl_is_split_at_020(lists):
    # The DDL range is the concrete half of the rule and the one incidents #150/#115 were about:
    # every file the checkout actually has must fall to one side, so a new one cannot arrive
    # unclaimed by either repo.
    unclaimed = []
    misfiled = []
    for sql in sorted(NEEDS_DDL.glob("*.sql")):
        relative = str(sql.relative_to(REPO_ROOT))
        numbered = re.match(r"(\d+)_", sql.name)
        assert numbered, f"{relative} does not start with a migration number"
        number = int(numbered.group(1))
        wanted = "fork-owned" if number >= FORK_DDL_FLOOR else "must-not-change"
        other = "must-not-change" if wanted == "fork-owned" else "fork-owned"
        if not any(_matches(pattern, relative) for pattern in lists[wanted]):
            unclaimed.append(f"{relative} should be {wanted}")
        if any(_matches(pattern, relative) for pattern in lists[other]):
            misfiled.append(f"{relative} is listed {other}")
    assert not unclaimed, unclaimed
    assert not misfiled, misfiled


def test_the_file_states_the_two_rules_in_prose(lists):
    # The lists say which files; these two sentences say what the rule is, and #192 names both.
    text = OWNERSHIP_MD.read_text(encoding="utf-8")
    assert "020" in text, "the DDL floor is not stated"
    assert "same wave" in text, "the shared-surface obligation is not stated"


def test_agents_md_points_at_the_three_enforcement_sites():
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for needle in ("contracts/ownership.md", "tests/test_ownership.py", "tool/checks/ownership"):
        assert needle in text, needle


# --- tool/checks/ownership -------------------------------------------------------------------

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env=CLEAN_ENV,
    )


@pytest.fixture
def fork_checkout(tmp_path: Path) -> Path:
    """A repo shaped like the fork: one file the fork owns and one it must not change."""
    repo = tmp_path / "fork"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True, env=CLEAN_ENV
    )
    (repo / "analysis" / "retrieval").mkdir(parents=True)
    (repo / "analysis" / "retrieval" / "bm25.py").write_text("# fork\n", encoding="utf-8")
    (repo / "db").mkdir()
    (repo / "db" / "migrate.sh").write_text("# upstream\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: seed")
    _git(repo, "branch", "base")
    return repo


def _run_check(repo: Path, ref: str = "base") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(OWNERSHIP_CHECK), ref], cwd=str(repo), capture_output=True, text=True, env=CLEAN_ENV
    )


def test_the_check_passes_when_only_fork_owned_files_changed(fork_checkout: Path):
    (fork_checkout / "analysis" / "retrieval" / "bm25.py").write_text("# changed\n", encoding="utf-8")
    _git(fork_checkout, "commit", "-aqm", "feat(retrieval): a file the fork owns")
    done = _run_check(fork_checkout)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_check_reports_a_forbidden_file_and_exits_1(fork_checkout: Path):
    (fork_checkout / "db" / "migrate.sh").write_text("# changed\n", encoding="utf-8")
    _git(fork_checkout, "commit", "-aqm", "chore: touch an upstream guard")
    done = _run_check(fork_checkout)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "db/migrate.sh" in done.stdout, done.stdout + done.stderr


def test_the_check_names_every_hit_on_its_own_line(fork_checkout: Path):
    # One line per hit: the audit block quotes this output verbatim, and a summary count there
    # would leave the reader without the file to look at.
    (fork_checkout / "db" / "migrate.sh").write_text("# changed\n", encoding="utf-8")
    (fork_checkout / "STATE.md").write_text("# not the fork's\n", encoding="utf-8")
    _git(fork_checkout, "add", "-A")
    _git(fork_checkout, "commit", "-qm", "chore: touch two upstream guards")
    done = _run_check(fork_checkout)
    assert done.returncode == 1, done.stdout + done.stderr
    hits = sorted(line.strip() for line in done.stdout.splitlines() if line.strip())
    assert hits == ["STATE.md", "db/migrate.sh"], done.stdout


def test_the_check_reads_the_three_dot_range(fork_checkout: Path):
    # `<ref>...HEAD`, not `<ref>..HEAD`: what upstream changed on its own side after the fork
    # branched is not something the fork did, and reporting it would make the check cry wolf.
    _git(fork_checkout, "checkout", "-q", "base")
    (fork_checkout / "db" / "migrate.sh").write_text("# upstream moved on\n", encoding="utf-8")
    _git(fork_checkout, "commit", "-aqm", "chore: upstream changes its own guard")
    _git(fork_checkout, "checkout", "-q", "main")
    done = _run_check(fork_checkout)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_check_refuses_a_ref_that_does_not_resolve(fork_checkout: Path):
    done = _run_check(fork_checkout, "upstream/main")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "upstream/main" in done.stderr, done.stderr
