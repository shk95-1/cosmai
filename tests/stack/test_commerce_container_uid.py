"""collector-commerce must not write into its bind-mounted profile as root (#27 round 2).

stack/Dockerfile declares no USER, so every *cron container is uid 0 by default. The profile bind
mount (test_commerce_browser_profile_volume.py) is host uid 1000 (user1's, and whatever the ops copy
of the old stack's warm profile lands as) -- root can still write it (root bypasses host permission
bits), but the files it creates then belong to root, and the next host-side `uv run cosmai login`
(uid 1000, not root) cannot touch them. That silently breaks the whole point of round 1's bind mount:
a person re-authorising from the host.

Scope is deliberately narrow: only collector-commerce touches a host-owned bind mount. The other
five *cron services only ever touch named volumes (youtube-payloads), which nothing but this fleet's
own containers ever reads, so widening the `user:` override to them would be a change nothing here
asked for.
"""

from __future__ import annotations

import re

from tests.stack.test_stack_wiring import SCHEDULED, SERVICES

ANCHOR_ENV_KEYS = ("COSMAI_DB_HOST", "COSMAI_DB_PORT", "COSMAI_SECRET_FILE", "PYTHONUNBUFFERED", "TZ")


def test_collector_commerce_runs_as_the_bind_mounts_host_uid():
    body = SERVICES["collector-commerce"]
    assert re.search(r'user:\s*"?1000:1000"?', body), (
        "collector-commerce has no user: 1000:1000 -- it still writes the profile bind mount as "
        "root, and a root-owned cookie file is one a host-side `cosmai login` cannot overwrite"
    )


def test_collector_commerce_sets_a_writable_home_for_that_uid():
    # uid 1000 has no /etc/passwd entry in this image; Chromium's home-directory lookup needs $HOME
    # set directly rather than falling back to a getpwuid() this image never populated.
    body = SERVICES["collector-commerce"]
    assert re.search(r"HOME:\s*/tmp\b", body), (
        "collector-commerce sets no HOME for uid 1000, which has no passwd entry to fall back to"
    )


def test_collector_commerce_still_gets_every_anchor_environment_key():
    # The same YAML merge trap test_commerce_browser_profile_volume.py pins for volumes: applies to
    # environment: too -- declaring the service's own mapping to add HOME replaces the anchor's
    # entirely unless every key is repeated by hand.
    body = SERVICES["collector-commerce"]
    missing = [k for k in ANCHOR_ENV_KEYS if not re.search(rf"{k}:\s*\S", body)]
    assert not missing, f"collector-commerce dropped anchor environment key(s): {missing}"


def test_no_other_scheduled_service_gets_a_user_override():
    others = sorted(set(SCHEDULED) - {"collector-commerce"})
    assert others, "this would pass vacuously if there were no other scheduled service to compare"
    leaked = [name for name in others if re.search(r"^\s*user:", SERVICES[name], re.M)]
    assert not leaked, f"user: override leaked onto service(s) that never asked for it: {leaked}"
