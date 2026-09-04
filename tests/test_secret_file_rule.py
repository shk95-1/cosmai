"""One rule for the secret file, and no password on a command line (#20).

`db/secrets.py` and `db/migrate.sh` read the same `~/.config/cosmai/env`. Two parsers of one file is
one parser too many the moment they disagree about a quoted or a padded value, so the table below is
answered by both and the two answers are compared -- rather than each being held against a
hand-written expectation, which is how they came to differ in the first place (#42 M3).

The other half of #20 is the exposure: a value handed to `psql -v` or to `docker exec -e` stands in
the host's `ps` for the length of the call. The second test therefore watches every argument
`db/migrate.sh` hands `docker` through a whole deploy, and asks that no secret value is among them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from db import secrets

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATE = REPO_ROOT / "db" / "migrate.sh"
KEY = "NEEDS_DB_RUNTIME"

# Each case is a whole file. The last five hold the rules a `grep "^KEY="` gets wrong.
FILES = [
    "NEEDS_DB_RUNTIME=plain",
    "NEEDS_DB_RUNTIME=with spaces inside",
    "NEEDS_DB_RUNTIME=trailing   ",
    "  NEEDS_DB_RUNTIME = padded  ",
    'NEEDS_DB_RUNTIME="double quoted"',
    "NEEDS_DB_RUNTIME='single quoted'",
    "NEEDS_DB_RUNTIME=unbalanced'",
    "NEEDS_DB_RUNTIME=a=b=c",
    "NEEDS_DB_RUNTIME=$pecial\\chars",
    "# NEEDS_DB_RUNTIME=commented\nNEEDS_DB_RUNTIME=real",
    "NEEDS_DB_RUNTIME=first\nNEEDS_DB_RUNTIME=last",
    "NEEDS_DB_RUNTIME=\nNEEDS_DB_MIGRATOR=other",
    "NEEDS_DB_MIGRATOR=other",
    "NEEDS_DB_RUNTIME no equals sign",
]


def _read_secret(path: Path, key: str, *, prefix: str = "needs") -> subprocess.CompletedProcess[str]:
    """`read_secret` as db/migrate.sh defines it, run over one file with the two variables the
    script sets around it."""
    program = f'set -u\nprefix="$1"\nsecret_file="$2"\n{_read_secret_source()}\nread_secret "$3"\n'
    return subprocess.run(
        ["sh", "-c", program, "sh", prefix, str(path), key], capture_output=True, text=True, check=False
    )


def _read_secret_source() -> str:
    """`read_secret` lifted out of db/migrate.sh, so this runs the deployed code rather than a copy
    of it. The slice raises if that function stops being a top-level one, which is the point."""
    text = MIGRATE.read_text(encoding="utf-8")
    start = text.index("read_secret() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


@pytest.mark.parametrize("body", FILES, ids=range(len(FILES)))
def test_the_shell_and_the_python_reader_agree_on_one_file(body: str, tmp_path: Path):
    path = tmp_path / "env"
    path.write_text(body + "\n", encoding="utf-8")
    # Both readers treat an empty value as no value, so both answers collapse to None there.
    expected = secrets.load(path).get(KEY) or None

    done = _read_secret(path, KEY)
    found = done.stdout if done.returncode == 0 else None
    assert found == expected, f"db/secrets.py says {expected!r}, db/migrate.sh says {found!r}"
    if expected is None:
        assert KEY in done.stderr, "the shell reader must name the key it could not find"
        assert body.replace("\n", " ") not in done.stderr, "and nothing else from the file"


def test_a_missing_collector_key_is_not_reported_as_a_needs_problem(tmp_path: Path):
    """Step (0) reads TREND_RADAR_DB_RUNTIME and TUBEDEPTH_DB_RUNTIME, and a message that calls a
    missing one a `needs` fault sends the reader to the wrong schema. The prefix is a variable the
    loop sets, so the same reader answers for whichever schema is being built."""
    path = tmp_path / "env"
    path.write_text("NEEDS_DB_RUNTIME=present\n", encoding="utf-8")
    done = _read_secret(path, "TREND_RADAR_DB_RUNTIME", prefix="trend_radar")
    assert done.returncode != 0
    assert done.stderr.startswith("trend_radar: missing key"), done.stderr
    assert "needs:" not in done.stderr


@pytest.mark.postgres
def test_no_secret_value_reaches_the_docker_command_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    harness_secrets: dict[str, str],
    deploy: Callable[..., subprocess.CompletedProcess[str]],
):
    """A shim ahead of docker on PATH writes down one line per argument and then execs the real one,
    so what this reads is exactly what `ps` would have shown. Grepping db/migrate.sh instead would
    measure the spelling of the call rather than the call.

    The deploy goes through the shared fixture, so it waits for needs_migrator's two connection
    slots the way every other in-suite deploy does (#178 review 4)."""
    real_docker = shutil.which("docker") or pytest.skip("docker is not on PATH")

    log = tmp_path / "argv"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "docker"
    shim.write_text(
        f'#!/bin/sh\nfor arg in "$@"; do printf \'%s\\n\' "$arg" >> "{log}"; done\nexec {real_docker} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ['PATH']}")
    done = deploy()
    assert done.returncode == 0, done.stderr
    arguments = log.read_text(encoding="utf-8").splitlines()
    assert any(a == "exec" for a in arguments), "the shim recorded nothing, so it proved nothing"

    # Every value the harness set carries, not a list written out beside it: a key added there and
    # forgotten here would leave this check green about one password fewer (#178 re-review 7).
    values = sorted(v for v in harness_secrets.values() if v)
    assert values, "no secret values to look for; this check would pass on anything"
    assert len(values) == len(harness_secrets), "a harness secret has no value to look for"
    exposed = [a for a in arguments for v in values if v in a]
    # The values are named nowhere in the failure: only how many arguments carried one.
    assert not exposed, f"{len(exposed)} argument(s) to docker carry a role password"
