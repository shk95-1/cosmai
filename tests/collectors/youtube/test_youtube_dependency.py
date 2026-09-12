"""yt-dlp is a capped runtime dependency and the image is asked for it (#183 Work 2).

Two failures this holds off, and neither of them says anything at 03:00.

**No cap.** yt-dlp breaks whenever YouTube changes something on its side, which is why the archive's
pyproject carried a cap and said so. An unbounded range lets `uv lock` pick up a release whose
extractor behaves differently from the one this transport was written against, and the symptom is a
collection that returns nothing for a reason that is not in this repository.

**Not in the image.** `uv sync --no-editable` builds the wheel from `[project] dependencies`; a
dependency that lives only in a dev extra, or only in the developer's venv, is simply absent from
the container the cron lines run in. Two of the four job kinds -- comments and transcripts -- have
no other route, so the collection fails on exactly the part this issue exists to restore.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
DOCKERFILE = (ROOT / "stack" / "Dockerfile").read_text(encoding="utf-8")


def _requirement(name: str) -> str:
    found = [line for line in PYPROJECT["project"]["dependencies"] if re.match(rf"^{name}\b", line.strip())]
    assert found, f"{name} is not a runtime dependency; a dev extra never reaches the image"
    return found[0]


def test_yt_dlp_is_a_runtime_dependency_and_not_a_dev_extra():
    assert _requirement("yt-dlp")


def test_the_yt_dlp_requirement_carries_an_upper_bound():
    requirement = _requirement("yt-dlp")
    assert "<" in requirement, (
        "yt-dlp breaks on YouTube-side changes; an unbounded range lets a lock refresh swap the "
        f"extractor under a transport that was measured against another one -- got {requirement!r}"
    )
    assert ">=" in requirement, f"a floor too: an old release stops extracting at all -- got {requirement!r}"


def test_the_lock_pins_a_version_inside_that_range():
    """`uv sync --frozen` in stack/Dockerfile installs the lock, not the range, so the lock is what
    actually ships."""
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert re.search(r'name = "yt-dlp"\nversion = "20\d\d\.\d+\.\d+"', lock)


def test_the_image_proves_yt_dlp_is_installed_at_build_time():
    """Asserted in the build rather than discovered by the first collection: an image that builds
    green and cannot extract is the shape of failure that costs a night."""
    assert "import yt_dlp" in DOCKERFILE
    assert "collectors.youtube.transport" in DOCKERFILE
