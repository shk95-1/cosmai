"""#226: the two repos' worktrees hashed the same 1000-port band and could collide by construction
-- a fork worktree once adopted an upstream suite's live container as its own leaked orphan and
removed it mid-run, because the holder check could only say "another worktree, or a leak", never
which.

Snippets are extracted from the real `tool/checks/test` and sourced in isolation with `git`/`docker`
faked as shell functions defined ahead of the snippet, not as executables on PATH -- a function is
looked up before PATH ever comes into it and needs no exec bit, so it needs no exec-bit or PATH
question answered to trust it. That was never actually the failure, though: two CI runs
(2026-09-06, 34030264845 then 34036753976) both produced the identical wrong port with a PATH shim
AND with the function. The real cause, found by reproducing it locally: `suite.yml` pins
`COSMAI_TEST_PG_PORT=55432` for the whole class-A job, and `_run_port` was copying that into the
faked subprocess's environment unfiltered, so `port=${COSMAI_TEST_PG_PORT:-...}` in the real script
took the override every time and never reached `$FAKE_ORIGIN` at all -- fixed by stripping that one
variable out of the inherited environment before adding the fake back in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE = REPO_ROOT / "tool" / "checks" / "test"


def _body() -> str:
    return SUITE.read_text(encoding="utf-8")


def _find(body: str, anchor: str, *, after: int = 0) -> int:
    """`str.index` that names the anchor on failure -- a bare ValueError just says a text moved
    somewhere in a 260-line file; this says which line this file expected to still be there."""
    try:
        return body.index(anchor, after)
    except ValueError:
        raise AssertionError(f"tool/checks/test no longer contains the expected anchor: {anchor!r}") from None


def _container_guard_body() -> str:
    """Everything between the container-branch guard and the postgres image line: comments, the
    repo-offset case and the port= assignment. No control flow of its own, so it sources safely
    without a matching `fi`."""
    body = _body()
    guard = 'if [ "$change_class" != C ] && [ "$change_class" != N ] && [ -z "$TEST_POSTGRES_URL" ]; then'
    start = _find(body, guard)
    start = _find(body, "\n", after=start) + 1
    end = _find(body, "image=${COSMAI_TEST_PG_IMAGE", after=start)
    return body[start:end]


def _holder_check_body() -> str:
    """The self-contained `holder=...; if ...; fi` block, ending right before the comment that
    introduces the slot-lock line."""
    body = _body()
    start = _find(body, 'holder=$(docker ps --filter "publish=$port"')
    end = _find(body, "# The slot is taken before the container is touched:", after=start)
    return body[start:end]


# A shell function shadows PATH entirely -- no exec bit, no lookup to skip past on a noexec tmp
# dir. $FAKE_ORIGIN travels through the environment rather than being baked into the function body,
# so the same function text serves every origin this file tries.
FAKE_GIT_FUNCTION = (
    'git() { if [ "$1 $2 $3" = "remote get-url origin" ]; '
    "then printf '%s\\n' \"$FAKE_ORIGIN\"; else return 1; fi; }\n"
)


def _run_port(tmp_path: Path, origin_url: str, **env: str) -> subprocess.CompletedProcess:
    # The SAME workdir every call: two calls in one test must hash the identical path, so only the
    # repo (FAKE_ORIGIN) differs between them.
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    script = FAKE_GIT_FUNCTION + _container_guard_body() + "\nprintf '%s\\n' \"$port\"\n"
    # CI's class-A job pins COSMAI_TEST_PG_PORT for its own single-runner container (suite.yml),
    # and this file's own test then inherits that override from os.environ -- both calls answered
    # the SAME pinned port regardless of $FAKE_ORIGIN, which is exactly what "the fake was not
    # consulted" looks like from outside (2026-09-06, CI runs 34030264845 and 34036753976; the
    # real cause was this leaked override, not a PATH/dash/noexec question). Dropped here unless a
    # caller re-adds it through **env, the one legitimate reason to set it (the override test below).
    clean_env = {k: v for k, v in os.environ.items() if k != "COSMAI_TEST_PG_PORT"}
    return subprocess.run(
        ["sh", "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
        env={**clean_env, "FAKE_ORIGIN": origin_url, **env},
    )


UPSTREAM_URL = "https://github.com/shk95-1/cosmai"
FORK_URL = "https://github.com/shk95/cosmai-import-ydc"


def test_upstream_and_fork_land_in_disjoint_bands(tmp_path: Path):
    upstream = _run_port(tmp_path, UPSTREAM_URL)
    fork = _run_port(tmp_path, FORK_URL)
    assert upstream.returncode == 0, upstream.stderr
    assert fork.returncode == 0, fork.stderr
    up_port = int(upstream.stdout.strip())
    fork_port = int(fork.stdout.strip())
    # If the fake `git` (or COSMAI_TEST_PG_PORT, see _run_port) were never actually consulted,
    # $repo_url comes back empty or unmatched and BOTH calls fall through to the same catch-all
    # offset -- the same port twice, silently, rather than a crash (#226 CI runs 34030264845 and
    # 34036753976 both did exactly this). Naming that failure mode directly keeps a future
    # regression to it from passing on the band checks alone.
    assert up_port != fork_port, "upstream and fork produced the identical port -- was git faked?"
    assert 55000 <= up_port <= 55499, f"upstream port {up_port} left its band"
    assert 55500 <= fork_port <= 55999, f"fork port {fork_port} left its band"


def test_the_offset_is_exactly_500_for_the_same_worktree_path(tmp_path: Path):
    # Same cwd both times, so the path hash is identical -- only the repo differs, and the two
    # ports must differ by exactly the offset, not by chance.
    upstream = _run_port(tmp_path, UPSTREAM_URL)
    fork = _run_port(tmp_path, FORK_URL)
    assert upstream.returncode == 0, upstream.stderr
    assert fork.returncode == 0, fork.stderr
    assert int(fork.stdout.strip()) - int(upstream.stdout.strip()) == 500


def test_cosmai_test_pg_port_still_overrides_the_derivation(tmp_path: Path):
    done = _run_port(tmp_path, FORK_URL, COSMAI_TEST_PG_PORT="12345")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "12345"


def test_the_container_is_labeled_with_the_worktree_path():
    """Static: running docker here would cost a container start to prove one flag exists."""
    body = _body()
    run_line_start = _find(body, "docker run -d --rm --name")
    run_line_end = _find(body, "\n", after=run_line_start)
    run_line = body[run_line_start:run_line_end]
    assert "--label" in run_line, "the container carries no worktree label"
    assert "cosmai.worktree=" in run_line
    assert "$(pwd -P)" in run_line, "the label must name THIS worktree, not a fixed string"


# Same reasoning as FAKE_GIT_FUNCTION: a function, not a PATH executable, needs no exec bit and is
# found before PATH is ever searched.
FAKE_DOCKER_FUNCTION = (
    "docker() {\n"
    '    case "$1" in\n'
    "        ps) printf 'cosmai-test-postgres-99999\\n' ;;\n"
    "        inspect) printf '%s\\n' \"$FAKE_HOLDER_PATH\" ;;\n"
    "    esac\n"
    "}\n"
)


def _run_holder_check(
    holder_path: str, port: str = "55842", name: str = "cosmai-test-postgres-55842"
) -> subprocess.CompletedProcess:
    script = FAKE_DOCKER_FUNCTION + f"port={port}\nname={name}\n" + _holder_check_body()
    return subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "FAKE_HOLDER_PATH": holder_path},
    )


HOLDER_PATH = "../cosmai-wt/contracts-206"


def test_a_labeled_holder_is_named_by_its_worktree_path():
    done = _run_holder_check(HOLDER_PATH)
    assert done.returncode == 1
    assert "held by another worktree" in done.stderr, done.stderr
    assert HOLDER_PATH in done.stderr, done.stderr
    assert "or a leak" not in done.stderr, "a labeled holder is not an unnamed leak"


def test_an_unlabeled_holder_falls_back_to_the_old_wording():
    # A container started before this change carries no label -- still a real holder, just an
    # older one, so the fallback message must still say so rather than crash on an empty path.
    done = _run_holder_check(holder_path="")
    assert done.returncode == 1
    assert "another worktree, or a leak" in done.stderr, done.stderr
