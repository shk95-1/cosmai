"""A deploy must not be a shadow run.

Review round 1 (#10, Important 2): `collectors/commerce/cli.py` now defaults to `live_fetchers()`,
so `collector-commerce` reaches four real sites from its next cron minute. STATE.md §3 forbids new
collection before the cutover, and until this file the only thing enforcing that was the convention
that nobody runs `docker compose up -d`. Condition 3 is a step a person opens at a chosen hour; a
gate is what makes opening it a decision rather than a side effect of deploying.

`collector-youtube-watch` set the precedent in the same compose file, for the same kind of reason
(policy, not capability), so the shape is copied rather than invented.

The compose file is asked through `docker compose config`, which resolves profiles the way `up`
does. Parsing the text ourselves would prove the key is spelled right and nothing about what compose
would actually start.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "stack" / "docker-compose.yml"
ROLLBACK = REPO_ROOT / "stack" / "rollback.sh"
COMMERCE_CLI = REPO_ROOT / "collectors" / "commerce" / "cli.py"

GATED = "collector-commerce"
PROFILE = "commerce"

# The claim the gate exists to keep true. It was written when every collector's default fetcher
# raised, and #10 made it false for commerce.
NO_REQUESTS_CLAIM = "외부 요청은 한 건도 나가지 않는다"


def _services(*profiles: str) -> set[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed; compose cannot answer what it would start")
    argv = ["docker", "compose"]
    for profile in profiles:
        argv += ["--profile", profile]
    done = subprocess.run(
        [*argv, "-f", str(COMPOSE), "config", "--services"],
        capture_output=True,
        text=True,
        # The compose file requires both host paths and defaults neither (#177 took the default
        # off the data directory). /dev/null is a real path that no container is ever created from
        # here -- `config` renders, it does not run.
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
            "COSMAI_SECRET_FILE_HOST": "/dev/null",
            "COSMAI_PG_DATA_DIR": "/dev/null",
        },
        check=False,
    )
    assert done.returncode == 0, f"docker compose config failed:\n{done.stderr}"
    return set(done.stdout.split())


def test_the_commerce_collector_would_reach_real_sites_the_moment_it_ran():
    """The premise. If this fails, the gate below is guarding nothing and should be reconsidered
    rather than kept out of habit."""
    text = COMMERCE_CLI.read_text(encoding="utf-8")
    assert "fetcher or live_fetchers()" in text, (
        "commerce no longer defaults to a live transport; this file's reason to exist is gone"
    )


def test_a_plain_up_does_not_start_the_live_collector():
    # `docker compose up -d` with no --profile is what a deploy runs. This is condition 3 not
    # happening by accident.
    assert GATED not in _services(), (
        f"{GATED} starts on a bare `docker compose up -d`, which makes deploying it a shadow run"
    )


def test_the_profile_is_what_opens_it():
    # And the gate has to be openable, by the name the compose comment tells an operator to use.
    assert GATED in _services(PROFILE)


@pytest.mark.parametrize("name", ["collector-naver", "collector-youtube-work", "analyze"])
def test_the_services_that_send_nothing_yet_are_still_wired_by_a_bare_up(name: str):
    # The ruling gates the arm that acquired a live transport, not collection wiring in general:
    # procedure 1 checks the wiring by bringing the stack up, and gating everything would leave it
    # nothing to compare.
    assert name in _services()


def test_the_compose_file_no_longer_claims_nothing_goes_out():
    # The comment that said this was true of `_RaisingFetcher`. A comment that is false about
    # external requests is worse than none: it is what an operator reads before deciding to deploy.
    assert NO_REQUESTS_CLAIM not in COMPOSE.read_text(encoding="utf-8"), (
        "stack/docker-compose.yml still claims no external request goes out, "
        f"but {COMMERCE_CLI.relative_to(REPO_ROOT)} defaults to a live transport"
    )


def test_the_compose_comment_tells_an_operator_which_profile_opens_it():
    text = COMPOSE.read_text(encoding="utf-8")
    assert f"--profile {PROFILE} up -d {GATED}" in text, (
        "a gated service whose comment does not spell the command to open it is a service nobody "
        "can open at condition 3"
    )


def test_rollback_enables_every_profile_the_compose_file_declares():
    """stack/rollback.sh stops the schedulers by name. compose does not know a service whose profile
    is not enabled, so a gated service missing from that command line is one left collecting through
    a rollback -- into the database the old stack has just been handed back."""
    declared = set(re.findall(r'^\s*profiles:\s*\["([\w.-]+)"\]', COMPOSE.read_text(encoding="utf-8"), re.M))
    assert declared, "no profile is declared; this check would pass on anything"
    enabled = set(re.findall(r"--profile\s+([\w.-]+)", ROLLBACK.read_text(encoding="utf-8")))
    assert declared <= enabled, f"stack/rollback.sh does not enable {sorted(declared - enabled)}"
