"""collector-commerce must not write into its bind-mounted profile as root (#27 round 2).

stack/Dockerfile declares no USER, so every *cron container is uid 0 by default. The profile bind
mount (test_commerce_browser_profile_volume.py) belongs to the configured host user. Root can still
write it (root bypasses host permission bits), but the files it creates then belong to root, and the
next host-side `uv run cosmai login` cannot touch them. That breaks the bind mount's purpose:
a person re-authorising from the host with the configured host UID.

Scope is deliberately narrow: only collector-commerce touches a host-owned bind mount. The other
five *cron services only ever touch named volumes (youtube-payloads), which nothing but this fleet's
own containers ever reads, so widening the `user:` override to them would be a change nothing here
asked for.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tests.stack.test_stack_wiring import SCHEDULED, SERVICES

ANCHOR_ENV_KEYS = ("COSMAI_DB_HOST", "COSMAI_DB_PORT", "COSMAI_SECRET_FILE", "PYTHONUNBUFFERED", "TZ")


def test_collector_commerce_runs_as_the_bind_mounts_host_uid():
    body = SERVICES["collector-commerce"]
    assert 'user: "${COSMAI_HOST_UID:-1000}:${COSMAI_HOST_GID:-1000}"' in body


def test_the_host_uid_override_reaches_the_actual_compose_service():
    compose = Path(__file__).resolve().parents[2] / "stack/docker-compose.yml"
    done = subprocess.run(
        ["docker", "compose", "--profile", "commerce", "-f", str(compose), "config", "--format", "json"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "COSMAI_SECRET_FILE_HOST": "/placeholder/env",
            "COSMAI_PG_DATA_DIR": "/placeholder/postgres",
            "COSMAI_HOST_UID": "2000",
            "COSMAI_HOST_GID": "100",
            "COSMAI_LLM_BUDGET_USD": "0",
            "COSMAI_LLM_CHAIN": "",
        },
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["services"]["collector-commerce"]["user"] == "2000:100"


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
