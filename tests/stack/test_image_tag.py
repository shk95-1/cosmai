"""One tag for the image this repo builds, named the same in every place that names it.

`cosmai` alone is not that tag: on the deploy host it already resolves to the archived fleet's app
image (`WorkingDir=/app/apps`, `ENTRYPOINT cosmai-entrypoint`), which the shared-db project is still
running. Both directions of that collision are silent -- `docker compose build` alone picks the
archived image up as a base instead of failing, and `docker build -t cosmai .` overwrites the tag
three running containers resolve. So the tag moved into a name this repo owns, and the two-step build
that produces it is written down in four places. This asks that those four still agree: a build
command that drifts from the ARG default it feeds is a build that succeeds and ships the wrong base.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The image stack/Dockerfile produces, and the one stack/Dockerfile.cron layers supercronic onto.
BASE_IMAGE = "cosmai-needs:local"
CRON_IMAGE = "cosmai-needs-cron:local"

# Every place that writes the base tag down, and how that place writes it.
BASE_SITES = {
    "stack/Dockerfile": r"-t (\S+) \.",
    "stack/Dockerfile.cron": r"ARG COSMAI_IMAGE=(\S+)",
    "tool/stack-build": r"-t (\S+) \.",
    "README.md": r"-t (\S+) \.",
}


def _text(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.is_file(), f"{relative} is one of the places that names the image tag, and is missing"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("site", sorted(BASE_SITES), ids=lambda s: s)
def test_every_place_that_names_the_base_image_names_the_same_tag(site: str):
    found = sorted(set(re.findall(BASE_SITES[site], _text(site))))
    assert found == [BASE_IMAGE], f"{site} names {found or 'no image tag'}, not [{BASE_IMAGE!r}]"


def test_the_compose_file_tags_the_scheduler_image_it_builds():
    compose = _text("stack/docker-compose.yml")
    assert f"image: {CRON_IMAGE}" in compose, f"stack/docker-compose.yml does not tag {CRON_IMAGE}"


@pytest.mark.parametrize(
    "site",
    [
        "stack/Dockerfile",
        "stack/Dockerfile.cron",
        "stack/docker-compose.yml",
        "README.md",
        "tool/stack-build",
    ],
    ids=lambda s: s,
)
def test_nothing_names_the_bare_cosmai_tag(site: str):
    # `cosmai` unqualified belongs to the archived fleet on the deploy host. A repo tag must carry
    # this repo's name and an explicit tag, so neither build direction can hit that image.
    bad = [
        line
        for line in _text(site).splitlines()
        if re.search(r"(?:-t|image:|FROM|COSMAI_IMAGE=)\s*cosmai(?![\w./-])", line)
    ]
    assert not bad, f"{site} names the archived fleet's `cosmai` tag: {bad}"
