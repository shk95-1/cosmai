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
from pathlib import Path
from urllib.parse import urlparse

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

    program = f'set -u\nsecret_file="$1"\n{_read_secret_source()}\nread_secret "$2"\n'
    done = subprocess.run(
        ["sh", "-c", program, "sh", str(path), KEY], capture_output=True, text=True, check=False
    )
    found = done.stdout if done.returncode == 0 else None
    assert found == expected, f"db/secrets.py says {expected!r}, db/migrate.sh says {found!r}"
    if expected is None:
        assert KEY in done.stderr, "the shell reader must name the key it could not find"
        assert body.replace("\n", " ") not in done.stderr, "and nothing else from the file"


def _harness_container() -> str:
    """The container tool/checks/test started, by the name it derives from the port."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    port = urlparse(url.replace("postgresql+psycopg://", "postgresql://")).port
    name = f"cosmai-test-postgres-{port}"
    probe = subprocess.run(["docker", "inspect", "-f", "{{.Name}}", name], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"{name} is not there -- running against an external TEST_POSTGRES_URL")
    return name


@pytest.mark.postgres
def test_no_secret_value_reaches_the_docker_command_line(tmp_path: Path):
    """A shim ahead of docker on PATH writes down one line per argument and then execs the real one,
    so what this reads is exactly what `ps` would have shown. Grepping db/migrate.sh instead would
    measure the spelling of the call rather than the call."""
    container = _harness_container()
    real_docker = shutil.which("docker") or pytest.skip("docker is not on PATH")
    # tool/checks/test's own dummy file is not exported to pytest, so this rebuilds it with the
    # values that harness wrote -- the roles already exist and keep those passwords.
    env_file = tmp_path / "env"
    env_file.write_text(
        "NEEDS_DB_MIGRATOR=check\nNEEDS_DB_RUNTIME=check-runtime\n"
        "TREND_RADAR_DB_RUNTIME=check-trend-radar\nTREND_RADAR_DB_READER=check-trend-radar-reader\n"
        "TUBEDEPTH_DB_RUNTIME=check-tubedepth\n",
        encoding="utf-8",
    )

    log = tmp_path / "argv"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "docker"
    shim.write_text(
        f'#!/bin/sh\nfor arg in "$@"; do printf \'%s\\n\' "$arg" >> "{log}"; done\nexec {real_docker} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}", "COSMAI_SECRET_FILE": str(env_file)}
    done = subprocess.run(
        ["db/migrate.sh", "--container", container, "--db", "fleet", "--superuser", "fleet"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    arguments = log.read_text(encoding="utf-8").splitlines()
    assert any(a == "exec" for a in arguments), "the shim recorded nothing, so it proved nothing"

    values = sorted(secrets.load(env_file).values())
    assert values, "the fixture file named no secrets; this check would pass on anything"
    exposed = [a for a in arguments for v in values if v in a]
    # The values are named nowhere in the failure: only how many arguments carried one.
    assert not exposed, f"{len(exposed)} argument(s) to docker carry a role password"
