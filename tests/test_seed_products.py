"""Error shape of the CSV -> row conversions in db/seed/products.py. No database."""

from __future__ import annotations

import pytest

from db.seed.products import _suncare


def test_a_member_without_a_colon_names_the_csv_and_row(tmp_path):
    slice_dir = tmp_path / "slice-suncare"
    slice_dir.mkdir()
    (slice_dir / "product_ref.csv").write_text(
        "product_ref,brand,name,first_seen,members\nsuncare:1,Brand,Name,,oy1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"slice-suncare/product_ref\.csv:2: member 'oy1' has no ':'"):
        _suncare(tmp_path)
