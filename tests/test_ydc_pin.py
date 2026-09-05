"""The one spot that catches pin/header divergence: every module header names a tag/sha the ydc repo
actually carries, and nothing points at the deleted slice directory without saying it is gone.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The four ydc repo tags (shk95-1/cosmai-ydc-old, formerly slopindustries/youtube-data-collector).
YDC_TAGS = {
    "v0.1.0": "02440ab",
    "v0.2.0": "969929f",
    "v0.3.0": "e5a1b00",
    "v0.4.0": "76db718",
}
PIN_TAG = "v0.4.0"

# Every file whose header may name a ydc origin: the promoted modules and the three comparison scripts
# (python with a shebang, no .py suffix -- outside a *.py glob, and the wave that raised the pin edited
# their headers unchecked).
SCANNED = (*sorted((ROOT / "analysis").rglob("*.py")), *sorted((ROOT / "tool").glob("compare-ydc-*")))

# Matches either `` `vX.Y.Z` `sha` `` or plain `vX.Y.Z sha`.
PAIR_RE = re.compile(r"`(v\d\.\d\.\d)`\s*`([0-9a-f]{7})`|(v\d\.\d\.\d)\s+([0-9a-f]{7})\b")
TAG_HINT_RE = re.compile(r"v0\.\d\.\d")


def _tag_tuple(tag: str) -> tuple[int, int, int]:
    major, minor, patch = tag[1:].split(".")
    return (int(major), int(minor), int(patch))


def test_versioning_md_names_exactly_one_import_pin():
    body = (ROOT / "contracts" / "versioning.md").read_text(encoding="utf-8")
    pin_lines = [line for line in body.splitlines() if "import pin" in line]
    assert len(pin_lines) == 1, pin_lines
    assert "`v0.4.0`" in pin_lines[0]
    assert "`76db718`" in pin_lines[0]


def test_every_header_tag_sha_pair_is_a_real_ydc_tag_not_newer_than_the_pin():
    pin_tuple = _tag_tuple(PIN_TAG)
    for path in SCANNED:
        text = path.read_text(encoding="utf-8")
        if "ydc" not in text or not TAG_HINT_RE.search(text):
            continue
        for match in PAIR_RE.finditer(text):
            tag = match.group(1) or match.group(3)
            sha = match.group(2) or match.group(4)
            assert tag in YDC_TAGS, f"{path}: unknown ydc tag {tag!r}"
            assert YDC_TAGS[tag] == sha, f"{path}: {tag} sha should be {YDC_TAGS[tag]!r}, found {sha!r}"
            assert _tag_tuple(tag) <= pin_tuple, f"{path}: tag {tag} is newer than the pin {PIN_TAG}"


def test_no_reference_to_the_deleted_slice_directory_is_left_unqualified():
    for path in SCANNED:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "analysis/slices/ydc/" not in line:
                continue
            window = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
            assert "deleted" in window or "pinned copy" in window, (
                f"{path}:{i + 1}: names the deleted slice directory without saying it is gone"
            )


# ---------- the bare-tag rule (#87) ----------


def _module_docstring(path: Path) -> str:
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")), clean=False) or ""


def bare_headers(paths: Iterable[Path]) -> list[str]:
    """The files whose module docstring names ydc and a tag but no tag/sha pair. A header is where the
    origin is named, and a tag with no sha beside it is exactly what `PAIR_RE` cannot check -- so it
    passed silently. Prose later in a file may name a tag alone ("changed later in v0.3.0")."""
    out = []
    for path in paths:
        doc = _module_docstring(path)
        hint = TAG_HINT_RE.search(doc)
        if "ydc" not in doc or hint is None or PAIR_RE.search(doc):
            continue
        out.append(f"{path}: the header names {hint.group(0)} with no sha beside it")
    return out


def test_the_comparison_scripts_are_in_the_scanned_set():
    names = {path.name for path in SCANNED}
    assert {"compare-ydc-crosscheck", "compare-ydc-holdout", "compare-ydc-sensitivity"} <= names


def test_a_header_that_names_a_ydc_tag_names_its_sha_too():
    assert bare_headers(SCANNED) == []


def test_a_bare_tag_in_a_header_is_caught(tmp_path: Path):
    bare = tmp_path / "promoted.py"
    bare.write_text('"""Promoted from ydc thing.py at v0.3.0, untouched."""\n', encoding="utf-8")
    paired = tmp_path / "paired.py"
    paired.write_text('"""Promoted from ydc thing.py, v0.3.0 e5a1b00."""\n', encoding="utf-8")
    assert bare_headers([paired]) == []
    assert bare_headers([bare]) == [f"{bare}: the header names v0.3.0 with no sha beside it"]
