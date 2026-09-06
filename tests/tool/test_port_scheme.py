"""#226: the two repos' worktrees hashed the same 1000-port band and could collide by construction
-- a fork worktree once adopted an upstream suite's live container as its own leaked orphan and
removed it mid-run, because the holder check could only say "another worktree, or a leak", never
which.

Snippets are extracted from the real `tool/checks/test` and sourced in isolation with a fake `git`
or `docker` on PATH: reaching this code for real costs a container start and a `db/migrate.sh` run
that answering "whose port band is this" and "what does the holder message say" does not need.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE = REPO_ROOT / "tool" / "checks" / "test"


def _body() -> str:
    return SUITE.read_text(encoding="utf-8")


def _container_guard_body() -> str:
    """Everything between the container-branch guard and the postgres image line: comments, the
    repo-offset case and the port= assignment. No control flow of its own, so it sources safely
    without a matching `fi`."""
    body = _body()
    guard = 'if [ "$change_class" != C ] && [ "$change_class" != N ] && [ -z "$TEST_POSTGRES_URL" ]; then'
    start = body.index(guard)
    start = body.index("\n", start) + 1
    end = body.index("image=${COSMAI_TEST_PG_IMAGE", start)
    return body[start:end]


def _holder_check_body() -> str:
    """The self-contained `holder=...; if ...; fi` block, ending right before the comment that
    introduces the slot-lock line."""
    body = _body()
    start = body.index('holder=$(docker ps --filter "publish=$port"')
    end = body.index("# The slot is taken before the container is touched:")
    return body[start:end]


def _fake_git(bin_dir: Path, origin_url: str) -> None:
    git = bin_dir / "git"
    git.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1 $2 $3\" = \"remote get-url origin\" ]; then printf '%s\\n' '{origin_url}'; "
        "else exit 1; fi\n",
        encoding="utf-8",
    )
    git.chmod(0o755)


def _run_port(tmp_path: Path, origin_url: str, **env: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / f"bin-{len(list(tmp_path.glob('bin-*')))}"
    bin_dir.mkdir()
    _fake_git(bin_dir, origin_url)
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    script = _container_guard_body() + "\nprintf '%s\\n' \"$port\"\n"
    return subprocess.run(
        ["sh", "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", **env},
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
    assert 55000 <= up_port <= 55499, f"upstream port {up_port} left its band"
    assert 55500 <= fork_port <= 55999, f"fork port {fork_port} left its band"


def test_the_offset_is_exactly_500_for_the_same_worktree_path(tmp_path: Path):
    # Same cwd both times, so the path hash is identical -- only the repo differs, and the two
    # ports must differ by exactly the offset, not by chance.
    upstream = _run_port(tmp_path, UPSTREAM_URL)
    fork = _run_port(tmp_path, FORK_URL)
    assert int(fork.stdout.strip()) - int(upstream.stdout.strip()) == 500


def test_cosmai_test_pg_port_still_overrides_the_derivation(tmp_path: Path):
    done = _run_port(tmp_path, FORK_URL, COSMAI_TEST_PG_PORT="12345")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "12345"


def test_the_container_is_labeled_with_the_worktree_path():
    """Static: running docker here would cost a container start to prove one flag exists."""
    body = _body()
    run_line_start = body.index("docker run -d --rm --name")
    run_line_end = body.index("\n", run_line_start)
    run_line = body[run_line_start:run_line_end]
    assert "--label" in run_line, "the container carries no worktree label"
    assert "cosmai.worktree=" in run_line
    assert "$(pwd -P)" in run_line, "the label must name THIS worktree, not a fixed string"


def _run_holder_check(
    tmp_path: Path, docker_script: str, port: str = "55842", name: str = "cosmai-test-postgres-55842"
) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(docker_script, encoding="utf-8")
    docker.chmod(0o755)
    script = f"port={port}\nname={name}\n" + _holder_check_body()
    return subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
    )


HOLDER_PATH = "../cosmai-wt/contracts-206"

FAKE_DOCKER_LABELED = f"""#!/bin/sh
case "$1" in
    ps) printf 'cosmai-test-postgres-99999\\n' ;;
    inspect) printf '{HOLDER_PATH}\\n' ;;
esac
"""

FAKE_DOCKER_UNLABELED = """#!/bin/sh
case "$1" in
    ps) printf 'cosmai-test-postgres-99999\\n' ;;
    inspect) printf '\\n' ;;
esac
"""


def test_a_labeled_holder_is_named_by_its_worktree_path(tmp_path: Path):
    done = _run_holder_check(tmp_path, FAKE_DOCKER_LABELED)
    assert done.returncode == 1
    assert "held by another worktree" in done.stderr, done.stderr
    assert HOLDER_PATH in done.stderr, done.stderr
    assert "or a leak" not in done.stderr, "a labeled holder is not an unnamed leak"


def test_an_unlabeled_holder_falls_back_to_the_old_wording(tmp_path: Path):
    # A container started before this change carries no label -- still a real holder, just an
    # older one, so the fallback message must still say so rather than crash on an empty path.
    done = _run_holder_check(tmp_path, FAKE_DOCKER_UNLABELED)
    assert done.returncode == 1
    assert "another worktree, or a leak" in done.stderr, done.stderr
