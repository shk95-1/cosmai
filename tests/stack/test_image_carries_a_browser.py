"""If a shipped source needs a real browser, the image has to carry one.

`uv sync` installs the playwright *package*; the Chromium it drives is a separate download, and
`playwright install` at first use would be a 150 MB fetch inside a cron container at 03:00 -- which,
on the source that is behind a challenge, is a run that reports blocked for a reason that has
nothing to do with the site. So the browser goes into the base image, and the build proves it.

This file is a coupling, not a spelling check: the requirement only exists while a registered source
declares `Transport.BROWSER`. Drop the browser transport and this relaxes; add a second browser
source and it still holds.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from collectors.commerce import sources as _sources  # noqa: F401 -- import registers every source
from collectors.commerce.contract import Transport
from collectors.commerce.registry import SOURCES

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "stack" / "Dockerfile"


def _needs_a_browser() -> bool:
    return any(cls.policy.transport is Transport.BROWSER for cls in SOURCES.values())


def _dockerfile() -> str:
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing; nothing defines the image"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_a_source_needs_a_browser_or_this_file_has_nothing_to_say():
    # Names the premise rather than leaving it implicit: if this ever fails, every assertion below
    # became vacuous and should be deleted with the transport.
    assert _needs_a_browser(), "no registered source declares Transport.BROWSER"


@pytest.mark.parametrize("package", ["httpx", "playwright"])
def test_the_live_transport_is_a_declared_dependency(package: str):
    if package == "playwright" and not _needs_a_browser():
        pytest.skip("no source needs a browser")
    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = [re.split(r"[<>=!~ \[]", spec, maxsplit=1)[0] for spec in declared["project"]["dependencies"]]
    assert package in names


def test_the_base_image_installs_the_browser_binary_and_its_os_packages():
    if not _needs_a_browser():
        pytest.skip("no source needs a browser")
    text = _dockerfile()
    install = [line for line in text.splitlines() if "playwright install" in line]
    assert install, "stack/Dockerfile installs the playwright package but never its browser"
    assert any("--with-deps" in line for line in install), (
        "Chromium needs shared libraries the slim base does not have; --with-deps is what brings them"
    )
    assert any("chromium" in line for line in install), (
        "only chromium is driven; the other two browsers are dead weight in the layer"
    )


def test_the_build_fails_rather_than_the_first_run_if_the_browser_is_missing():
    """The last RUN in stack/Dockerfile is the evidence for #10 cutover condition 2 ("it works inside
    the image"). A browser that is only exercised at 03:00 is not covered by that evidence."""
    if not _needs_a_browser():
        pytest.skip("no source needs a browser")
    text = _dockerfile()
    verifying = [line for line in text.splitlines() if "executable_path" in line and "chromium" in line]
    assert verifying, (
        "no build step resolves the chromium executable, so an image with the package and no browser "
        "builds green and fails at the first scheduled run"
    )


def test_the_browsers_live_outside_any_home_directory():
    if not _needs_a_browser():
        pytest.skip("no source needs a browser")
    text = _dockerfile()
    assert re.search(r"PLAYWRIGHT_BROWSERS_PATH=\S+", text), (
        "without this the browser lands in root's ~/.cache and is unreadable to any other user"
    )
