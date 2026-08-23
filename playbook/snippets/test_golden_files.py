"""origin: service/Research_Paper/tests/paper_radar/test_trend_golden.py:41-60
reuse: set FIXTURE_IN, GOLDEN_DIR, GOLDEN_FILES and `run_pipeline`; keep the byte comparison --
the output files are a public surface. Regenerate goldens only when the INPUT fixture changes, and
say so in the commit body (after regeneration the test compares the code with itself).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_IN = ROOT / "fixtures" / "eval_synthetic"
GOLDEN_DIR = ROOT / "fixtures" / "eval_golden"
GOLDEN_FILES = ("product_links.csv", "need_mentions.csv")


def run_pipeline(input_dir: Path, out_dir: Path) -> None:
    """Replace with the real entry point: read input_dir, write GOLDEN_FILES into out_dir."""
    raise NotImplementedError


def _assert_files_equal(expected: Path, actual: Path) -> None:
    e, a = expected.read_bytes(), actual.read_bytes()
    if e == a:
        return
    el, al = e.decode("utf-8-sig").splitlines(), a.decode("utf-8-sig").splitlines()
    for n, (x, y) in enumerate(zip(el, al, strict=False), start=1):
        if x != y:
            pytest.fail(f"{expected.name} line {n}:\n  expected: {x!r}\n  actual:   {y!r}")
    pytest.fail(f"{expected.name}: line count {len(el)} vs {len(al)}")


def test_goldens_exist():
    assert all((GOLDEN_DIR / f).is_file() for f in GOLDEN_FILES), sorted(GOLDEN_DIR.glob("*"))


@pytest.mark.parametrize("name", GOLDEN_FILES)
def test_output_is_byte_identical_to_golden(tmp_path: Path, name: str):
    run_pipeline(FIXTURE_IN, tmp_path)
    _assert_files_equal(GOLDEN_DIR / name, tmp_path / name)
