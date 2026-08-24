"""collector-commerce needs a place to keep the browser profile a person authorises by hand (#27).

Round 1 (user decision, 2026-08-24): a bind mount, not a named volume -- the old stack did the same
thing for the same reason (service/stack/docker-compose.yml:156's TREND_RADAR_PROFILE_DIR), because a
named volume forces the login window through the container, and on WSL2 that means wiring up display
forwarding nobody already has. `collector-youtube-work`/`collector-youtube-flatten` already hit the
YAML trap this file pins regardless of mount kind: `<<:` on a service that also declares `volumes:`
drops the anchor's list outright, taking the read-only secret mount with it.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.stack.test_stack_wiring import SERVICES

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "stack" / "docker-compose.yml"
COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")

SECRET_MOUNT_RE = r"- \$\{[\w]+[^}]*}:/run/cosmai/env:ro"
KNOB = "COMMERCE_BROWSER_PROFILE_DIR"
CONTAINER_PATH = "/srv/cosmai/var/browser-profiles"


def _profile_mount_line() -> str | None:
    match = re.search(
        rf"- \$\{{{KNOB}:-([^}}]*)}}:{re.escape(CONTAINER_PATH)}(:ro)?\s*$",
        SERVICES["collector-commerce"],
        re.M,
    )
    return match.group(0) if match else None


def test_collector_commerce_declares_a_browser_profile_bind_mount_through_an_env_knob():
    line = _profile_mount_line()
    assert line, (
        f"collector-commerce has no ${{{KNOB}:-...}}:{CONTAINER_PATH} bind mount; a person's "
        "`cosmai login` leaves cookies nowhere the container keeps"
    )


def test_the_bind_mount_is_read_write():
    # oliveyoung refreshes its session cookies mid-run (issue #27's ops note); read-only would make
    # every collection after the first one walk against a cookie the site has already rotated.
    line = _profile_mount_line()
    assert line is not None
    assert not line.rstrip().endswith(":ro"), "the browser profile mount must not be read-only"


def test_collector_commerce_keeps_its_secret_mount_alongside_the_profile_mount():
    # The trap this file exists to catch: `<<: *cron` merged with a service's own `volumes:` drops
    # the anchor's volumes entirely (test_stack_wiring.py's
    # test_a_service_key_replaces_the_anchors_rather_than_extending_it proves the mechanism). Adding
    # the profile mount without repeating this line is a silently unreadable secret file.
    body = SERVICES["collector-commerce"]
    assert re.search(SECRET_MOUNT_RE, body), (
        "collector-commerce declares volumes: but dropped the read-only secret mount the *cron "
        "anchor used to provide -- YAML merge-key semantics replace, they do not extend"
    )


def test_the_knobs_default_resolves_to_the_same_directory_a_host_login_would_use():
    """`cosmai login`, run from the repo root on the host, writes to
    `collectors/commerce/transport/browser.py`'s DEFAULT_PROFILE_DIR -- a path relative to cwd. This
    bind mount's default has to resolve to that same directory, or a person's login and the
    scheduled collector authorise two different places. Compose resolves a relative bind-mount host
    path against the *compose file's own directory* (stack/), which is what the arithmetic below
    reproduces without invoking docker."""
    line = _profile_mount_line()
    assert line is not None
    default_match = re.search(rf"\$\{{{KNOB}:-([^}}]*)}}", line)
    assert default_match is not None
    default = default_match.group(1)
    assert not default.startswith("/"), "the default should be relative, like the old stack's was"
    resolved = (COMPOSE.parent / default).resolve()
    assert resolved == REPO_ROOT / "var" / "browser-profiles", (
        f"{KNOB}'s default {default!r} resolves to {resolved}, not the repo root's "
        "var/browser-profiles that a host-run `cosmai login` would use"
    )


def test_no_named_volume_remains_for_the_browser_profile():
    # Round 1 replaced the named volume with a bind mount; a name left behind in the top-level
    # volumes: block would be a stale, unused declaration nothing ever mounts.
    assert "\nvolumes:\n" in COMPOSE_TEXT, "stack/docker-compose.yml's top-level volumes: block is missing"
    block = COMPOSE_TEXT.split("\nvolumes:\n", 1)[1].split("\nnetworks:", 1)[0]
    names = set(re.findall(r"^  ([\w.-]+):\s*$", block, re.M))
    assert names == {"youtube-payloads"}, (
        f"unexpected named volume(s) declared: {names - {'youtube-payloads'}}"
    )


def test_env_example_documents_the_knob():
    env_example = (REPO_ROOT / "stack" / "env.example").read_text(encoding="utf-8")
    assert f"{KNOB}=" in env_example, f"stack/env.example never mentions {KNOB}"
