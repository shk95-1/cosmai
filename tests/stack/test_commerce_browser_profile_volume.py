"""collector-commerce needs a place to keep the browser profile a person authorises by hand (#27).

Without a volume the profile lives in the container's writable layer and is gone on the next
`docker compose up` -- which quietly turns condition 3's shadow run back into a cold start every
time the image is rebuilt. `collector-youtube-work`/`collector-youtube-flatten` already hit the same
YAML trap (`<<:` on a service that also declares `volumes:` drops the anchor's list outright, taking
the read-only secret mount with it) and are the precedent this test pins: a service adding its own
volume must re-declare the secret mount alongside it, not lose it.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.stack.test_stack_wiring import SERVICES

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "stack" / "docker-compose.yml"
COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")

SECRET_MOUNT_RE = r"- \$\{[\w]+[^}]*}:/run/cosmai/env:ro"


def test_collector_commerce_declares_a_browser_profile_volume():
    body = SERVICES["collector-commerce"]
    assert re.search(r"- [\w.-]+:/srv/cosmai/var/browser-profiles\b", body), (
        "collector-commerce has no browser-profile volume; a person's `cosmai login` "
        "leaves cookies nowhere the container keeps"
    )


def test_collector_commerce_keeps_its_secret_mount_alongside_the_new_volume():
    # The trap this file exists to catch: `<<: *cron` merged with a service's own `volumes:` drops
    # the anchor's volumes entirely (test_stack_wiring.py's
    # test_a_service_key_replaces_the_anchors_rather_than_extending_it proves the mechanism). Adding
    # the profile volume without repeating this line is a silently unreadable secret file.
    body = SERVICES["collector-commerce"]
    assert re.search(SECRET_MOUNT_RE, body), (
        "collector-commerce declares volumes: but dropped the read-only secret mount the *cron "
        "anchor used to provide -- YAML merge-key semantics replace, they do not extend"
    )


def test_the_browser_profile_volume_is_registered_at_the_file_bottom():
    assert "\nvolumes:\n" in COMPOSE_TEXT, "stack/docker-compose.yml's top-level volumes: block is missing"
    block = COMPOSE_TEXT.split("\nvolumes:\n", 1)[1].split("\nnetworks:", 1)[0]
    names = set(re.findall(r"^  ([\w.-]+):\s*$", block, re.M))
    used = set(re.findall(r"- ([\w.-]+):/srv/cosmai/", SERVICES["collector-commerce"]))
    assert used, "collector-commerce names no named volume by this pattern"
    assert used <= names, f"{used - names} used by collector-commerce but never declared in volumes:"
