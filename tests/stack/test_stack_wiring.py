"""What stack/docker-compose.yml claims and what the rest of the repo can actually answer.

playbook 02-test-discipline.md T10, 재사용 형태: the two repos that had this test still shipped the
2026-08 outage, because their copy read the compose file inside the repo while the real wiring lived
in another repo's stack/. Here there is only one stack/, so one file can ask all of it: every cron
line names a subcommand the CLI has, every service names a crontab file that exists, every service
that runs one reaches the database the same way, and no secret value is written down anywhere.

Parsed by hand rather than with PyYAML: this repo has no yaml dependency and adding one to read a
file we ourselves keep regular would be the larger change. `<<: *anchor` is resolved the way YAML
resolves it -- a key the service declares wins outright and the anchor's copy of that key is gone --
which is what the "this block declares X" questions asked below need to be about the real file.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "stack" / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / "stack" / "env.example"
CRONTAB_D = REPO_ROOT / "stack" / "crontab.d"
DOCKERFILE_CRON = REPO_ROOT / "stack" / "Dockerfile.cron"
SECRETS_MD = REPO_ROOT / "contracts" / "secrets.md"

# stack/Dockerfile's WORKDIR: the image carries the checkout there, so a container path is a repo
# path with this prefix.
IMAGE_ROOT = "/srv/cosmai/"
SCHEDULER = "supercronic"
DB_HOST = "postgres"
DB_PORT = "5432"
# #177: this compose owns the database now. The container keeps a cosmai- name like every
# other one here, and the old fleet's name survives only as a network alias.
DB_SERVICE = "postgres"
DB_CONTAINER = "cosmai-postgres"
DB_IMAGE = "postgres:18"
DB_PUBLISHED = "127.0.0.1:5434:5432"
DB_DATA_IN_CONTAINER = "/var/lib/postgresql"
TRANSITION_ALIAS = "shared-postgres"
SECRET_IN_CONTAINER = "/run/cosmai/env"

COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")

_ANCHOR = re.compile(r"^(x-[\w.-]+): &([\w.-]+)\s*$")
_SERVICE = re.compile(r"^  ([\w.-]+):\s*$")


def _blocks(text: str, header: re.Pattern[str], *, under: str | None) -> dict[str, str]:
    """`{name: body}` for every block whose opening line matches `header`, a body being the lines up
    to the next block at the same indent. `under` limits the scan to one top-level key's subtree."""
    out: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    inside = under is None
    for line in text.splitlines():
        if line and not line[0].isspace():
            if under is not None:
                inside = line.startswith(f"{under}:")
            if name is not None and not header.match(line):
                out[name], name, buf = "\n".join(buf), None, []
        if not inside:
            continue
        if match := header.match(line):
            if name is not None:
                out[name] = "\n".join(buf)
            name, buf = match.group(1), []
            continue
        if name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf)
    return out


def _anchor_bodies(text: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line and not line[0].isspace():
            if name is not None:
                bodies[name], name, buf = "\n".join(buf), None, []
            if match := _ANCHOR.match(line):
                name, buf = match.group(2), []
            continue
        if name is not None:
            buf.append(line)
    if name is not None:
        bodies[name] = "\n".join(buf)
    return bodies


def _keyed(body: str) -> dict[str, str]:
    """`{key: block}` for the mapping keys at a block body's own indent level, a block being the key
    line plus everything nested under it."""
    lines = body.splitlines()
    depths = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not depths:
        return {}
    level = min(depths)
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and len(line) - len(line.lstrip()) == level:
            if key is not None:
                out[key] = "\n".join(buf)
            # `<<: *anchor` and list items sit at this level too and open no key of their own.
            match = re.match(r"([\w.-]+):", stripped)
            key, buf = (match.group(1), [line]) if match else (None, [])
            continue
        if key is not None:
            buf.append(line)
    if key is not None:
        out[key] = "\n".join(buf)
    return out


def _merged_services(text: str) -> dict[str, str]:
    """Each service's body merged with the anchors it names, under YAML merge-key semantics: a key
    the service declares **replaces** the anchor's rather than extending it. Concatenating instead
    made every "this service declares X" check below vacuous for the three services that write their
    own `volumes:` -- their secret mount could be deleted and nothing here would notice."""
    bodies = _anchor_bodies(text)
    out = {}
    for name, body in _blocks(text, _SERVICE, under="services").items():
        merged = _keyed(body)
        for anchor in re.findall(r"<<:\s*\*([\w.-]+)", body):
            assert anchor in bodies, f"service {name} merges unknown anchor *{anchor}"
            for anchor_key, block in _keyed(bodies[anchor]).items():
                merged.setdefault(anchor_key, block)
        out[name] = "\n".join(merged.values())
    return out


SERVICES = _merged_services(COMPOSE_TEXT)


def test_a_service_key_replaces_the_anchors_rather_than_extending_it():
    """YAML merge-key semantics, which every check below stands on: a service that declares its own
    `volumes:` gets **none** of the anchor's. Modelling `<<:` as concatenation made the secret-mount
    check vacuous -- deleting a service's own mount line left the whole file green."""
    text = textwrap.dedent(
        """\
        x-cron: &cron
          volumes:
            - anchor-only:/anchor
          environment:
            KEEP: "1"

        services:
          own:
            <<: *cron
            volumes:
              - service-own:/service
          inherits:
            <<: *cron
        """
    )
    merged = _merged_services(text)
    assert "/service" in merged["own"]
    assert "/anchor" not in merged["own"], (
        "a service that declares volumes: does not also get the anchor's -- YAML drops them"
    )
    assert 'KEEP: "1"' in merged["own"], "keys the service does not declare still come from the anchor"
    assert "/anchor" in merged["inherits"]


# `command: ["supercronic", "<path>"]` -- the services this file is really about.
SCHEDULED = {
    name: match.group(1)
    for name, body in SERVICES.items()
    if (match := re.search(rf'{SCHEDULER}",\s*"([^"]+)"', body))
}
CRON_FILES = sorted(p for p in CRONTAB_D.iterdir() if p.is_file())


def _cron_argv() -> list[tuple[str, list[str]]]:
    argv = []
    for path in CRON_FILES:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                argv.append((path.name, line.split()[5:]))
    return argv


CRON_ARGV = _cron_argv()


def help_for(*args: str) -> str:
    """`cosmai <args> --help` in a bare environment -- offline, and it proves the console entrypoint
    parses the same argv supercronic will hand it."""
    done = subprocess.run(
        [sys.executable, "-m", "cosmai.cli", *args, "--help"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "COLUMNS": "200"},
        cwd=REPO_ROOT,
        check=False,
    )
    assert done.returncode == 0, f"cosmai {' '.join(args)} --help failed:\n{done.stderr}"
    return done.stdout


def test_there_are_scheduled_services_and_cron_lines():
    assert SCHEDULED and CRON_ARGV


@pytest.mark.parametrize("name", sorted(SCHEDULED), ids=lambda n: n)
def test_a_scheduled_service_runs_the_crontab_named_after_it(name: str):
    # The naming rule is the whole index: with one file per container there is no other way to see,
    # from either side, that a container is running the schedule someone meant it to run.
    path = SCHEDULED[name]
    assert path == f"{IMAGE_ROOT}stack/crontab.d/{name}", (
        f"service {name} runs {path}; a scheduled service runs stack/crontab.d/<its own name>"
    )
    assert (REPO_ROOT / path[len(IMAGE_ROOT) :]).is_file(), f"{path} is not a file in this repo"


@pytest.mark.parametrize("path", CRON_FILES, ids=lambda p: p.name)
def test_every_crontab_file_has_a_service_running_it(path: Path):
    assert path.name in SCHEDULED, (
        f"stack/crontab.d/{path.name} has no compose service running it -- its lines never fire"
    )


@pytest.mark.parametrize("case", CRON_ARGV, ids=lambda c: f"{c[0]}:{' '.join(c[1][:3])}")
def test_every_cron_line_names_a_command_the_cli_has(case: tuple[str, list[str]]):
    _, argv = case
    assert argv[0] == "cosmai", f"a cron line runs {argv[0]!r}; only the cosmai entrypoint is wired"
    text = help_for(argv[1])
    for option in (a for a in argv[2:] if a.startswith("--")):
        assert re.search(rf"(^|\s){re.escape(option)}(\s|$|,)", text), (
            f"cosmai {argv[1]} has no option {option}"
        )


@pytest.mark.parametrize("name", sorted(SCHEDULED), ids=lambda n: n)
def test_a_scheduled_service_reaches_the_database_by_the_contracted_knobs(name: str):
    # contracts/entrypoints.md §DB 접속 노브: inside the compose network the same database is
    # postgres:5432 -- the service this file now declares (#177). Missing knobs do not fail loudly
    # -- db/runtime.py falls back to the host defaults 127.0.0.1:5434, which inside a container is
    # nothing at all.
    body = SERVICES[name]
    assert f"COSMAI_DB_HOST: {DB_HOST}" in body, f"{name} does not set COSMAI_DB_HOST={DB_HOST}"
    assert re.search(rf'COSMAI_DB_PORT: "?{DB_PORT}"?', body), f"{name} does not set COSMAI_DB_PORT"


@pytest.mark.parametrize("name", sorted(SCHEDULED), ids=lambda n: n)
def test_a_scheduled_service_reads_its_crontab_in_utc(name: str):
    # Every file in stack/crontab.d/ opens with "UTC." and every time in contracts/entrypoints.md
    # §스케줄 is UTC. Without TZ set that is a property of whatever the base image happens to ship,
    # and a base image change moves six schedules at once with nothing here noticing.
    assert "TZ: UTC" in SERVICES[name], f"{name} does not pin TZ=UTC, which its crontab assumes"


@pytest.mark.parametrize("path", CRON_FILES, ids=lambda p: p.name)
def test_every_crontab_file_says_which_zone_its_times_are_in(path: Path):
    assert path.read_text(encoding="utf-8").startswith("# UTC"), (
        f"stack/crontab.d/{path.name} does not open by naming its timezone"
    )


@pytest.mark.parametrize("name", sorted(SCHEDULED), ids=lambda n: n)
def test_a_scheduled_service_gets_its_secrets_by_read_only_mount(name: str):
    body = SERVICES[name]
    assert f"COSMAI_SECRET_FILE: {SECRET_IN_CONTAINER}" in body, (
        f"{name} does not point db/secrets.py at {SECRET_IN_CONTAINER}"
    )
    assert re.search(rf"- \$\{{[\w]+[^}}]*}}:{SECRET_IN_CONTAINER}:ro", body), (
        f"{name} must bind the host secret file at {SECRET_IN_CONTAINER} read-only, "
        "through a variable rather than a literal host path"
    )


def test_this_compose_owns_the_shared_network():
    # #177 flips the ownership the file was written under: the shared-db project that created
    # db-net is dead and its compose file is gone, so nothing else declares this network any more.
    # `external: true` would now mean `up` fails on a host where nobody made it first.
    block = _blocks(COMPOSE_TEXT, re.compile(r"^  ([\w.-]+):\s*$"), under="networks")
    assert "db-net" in block, "stack/docker-compose.yml declares no db-net"
    assert "name: db-net" in block["db-net"], (
        "db-net must keep its unprefixed name -- old-stack containers are attached to it by that name"
    )
    declared = [ln.strip() for ln in block["db-net"].splitlines() if not ln.strip().startswith("#")]
    assert not any(ln.startswith("external") for ln in declared), (
        "the shared-db project that owned db-net is gone (#176); this compose creates it now"
    )


def test_the_compose_file_declares_the_database_it_talks_to():
    assert DB_SERVICE in SERVICES, (
        f"stack/docker-compose.yml declares no {DB_SERVICE} service, so COSMAI_DB_HOST={DB_HOST} "
        "names nothing on this network"
    )


def test_the_database_service_serves_the_cluster_that_is_already_on_disk():
    """The old fleet's container is gone but its data directory is not, and every row this repo has
    is in it. Image tag, mount point and published port are what aim the service at that cluster;
    what makes a wrong aim loud instead of silent is the separate absence checked below."""
    body = SERVICES[DB_SERVICE]
    assert f"container_name: {DB_CONTAINER}" in body, f"{DB_SERVICE} is not named {DB_CONTAINER}"
    assert f"image: {DB_IMAGE}" in body, (
        f"the cluster on disk was written by {DB_IMAGE}; another major would refuse to start on it"
    )
    assert re.search(rf"- \$\{{COSMAI_PG_DATA_DIR:\?[^}}]+}}:{DB_DATA_IN_CONTAINER}\b", body), (
        f"{DB_SERVICE} must bind the host data directory at {DB_DATA_IN_CONTAINER} -- the parent, "
        "so PGDATA's 18/docker below it is found -- through a variable with NO default: `name: "
        "cosmai` and container_name are fixed, so a default that resolves per checkout lets an "
        "`up` from a worktree recreate the production container over the wrong directory"
    )
    assert re.search(rf'- "{re.escape(DB_PUBLISHED)}"', body), (
        f"{DB_SERVICE} must publish {DB_PUBLISHED} -- db/runtime.py's host default is 5434"
    )
    assert "healthcheck:" in body and "pg_isready" in body, (
        f"{DB_SERVICE} needs a healthcheck; the depends_on conditions below wait on it"
    )


# The five the entrypoint reads before it decides whether to run initdb.
INITDB_VARS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_PASSWORD_FILE",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_HOST_AUTH_METHOD",
)


@pytest.mark.parametrize("var", INITDB_VARS, ids=lambda v: v)
def test_the_database_service_sets_nothing_initdb_would_need(var: str):
    """The absence is the safety net, not the mount path. The cluster on disk is initialised, so
    the entrypoint never asks for these and setting one changes nothing on a good day -- but it
    turns a mistyped COSMAI_PG_DATA_DIR from `exit 1` into a brand-new empty cluster that
    pg_isready reports healthy and all six schedulers then write into. contracts/secrets.md names
    no POSTGRES_ key, so test_no_secret_key_is_given_a_value_in_the_repo does not cover them."""
    assert var not in SERVICES[DB_SERVICE], (
        f"{DB_SERVICE} names {var}; the cluster already exists and initdb must stay unreachable"
    )


def test_the_database_service_answers_to_the_old_fleets_name_during_the_transition():
    # The old stack's PostgREST still resolves the database as shared-postgres on this network and
    # the portal still calls it from the browser (#179). The alias is what keeps that name resolving
    # while it does; #181 removes both.
    body = SERVICES[DB_SERVICE]
    assert re.search(rf"aliases:\s*\n\s*- {TRANSITION_ALIAS}\b", body), (
        f"{DB_SERVICE} does not answer to {TRANSITION_ALIAS} on db-net"
    )
    assert "#181" in body, "the transition alias must say which issue removes it"


@pytest.mark.parametrize("name", sorted(set(SCHEDULED) | {"portal"}), ids=lambda n: n)
def test_a_service_waits_for_the_database_to_be_healthy(name: str):
    # Without the condition, a cron container that starts while the cluster is still replaying WAL
    # runs its next line against a refused connection -- a failed run, not a crash loop, so nothing
    # restarts it and the schedule just loses that slot.
    assert re.search(
        rf"depends_on:\s*\n\s*{DB_SERVICE}:\s*\n\s*condition: service_healthy", SERVICES[name]
    ), f"{name} does not wait for {DB_SERVICE} to be healthy"


def secret_key_names() -> list[str]:
    """Every key contracts/secrets.md names, as backticked ALL-CAPS tokens."""
    return sorted(set(re.findall(r"`([A-Z][A-Z0-9_]{4,})`", SECRETS_MD.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("path", [COMPOSE, ENV_EXAMPLE, DOCKERFILE_CRON, *CRON_FILES], ids=lambda p: p.name)
def test_no_secret_key_is_given_a_value_in_the_repo(path: Path):
    # contracts/secrets.md: values live in ~/.config/cosmai/env and nowhere else. A placeholder
    # counts -- it is the line someone edits in place and then commits.
    keys = secret_key_names()
    assert keys, "contracts/secrets.md named no keys; this check would pass on anything"
    assigned = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
        for key in keys
        if re.search(rf"\b{key}\s*[:=]\s*\S", line)
    ]
    assert not assigned, f"{path.name} gives a secret key a value: {assigned}"


def test_the_compose_file_hardcodes_no_host_path():
    bad = [ln for ln in COMPOSE_TEXT.splitlines() if re.search(r"(?<!\w)/(home|root|Users)/", ln)]
    assert not bad, f"host-absolute paths belong in stack/env.example's variables, not here: {bad}"


def test_rollback_stops_exactly_the_scheduled_services():
    """stack/rollback.sh names its services in a list; a seventh scheduler added to compose and not
    to that list would leave one cron container running through a rollback -- collecting into a
    database the old stack has just been handed back."""
    text = (REPO_ROOT / "stack" / "rollback.sh").read_text(encoding="utf-8")
    match = re.search(r"^new_services='([^']*)'", text, re.MULTILINE)
    assert match, "stack/rollback.sh no longer declares new_services='...'"
    assert set(match.group(1).split()) == set(SCHEDULED), (
        "stack/rollback.sh stops "
        f"{sorted(match.group(1).split())}, but the scheduled services are {sorted(SCHEDULED)}"
    )
