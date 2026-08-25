"""Loads the 2026-08-23 eval sets and analysis-slice CSVs into the `needs` schema."""

from __future__ import annotations

from pathlib import Path

from db.seed import labeled, lexicon, mentions, metrics, panel, products
from db.seed._common import DEFAULT_SLICES, EVAL_DIR, connect

# Order matters: product_ref before the mentions that reference it, analysis_run before the metrics.
GROUP_NAMES = ("lexicon", "panel", "labeled", "products", "mentions", "metrics")


def run_all(
    url: str, slices: str | Path | None = None, only: tuple[str, ...] = GROUP_NAMES
) -> dict[str, int]:
    """Loads each requested group and returns the resulting `SELECT count(*)` per table."""
    slices_dir = Path(slices) if slices else DEFAULT_SLICES
    loaders = {
        "lexicon": (lexicon.load, EVAL_DIR),
        "panel": (panel.load, EVAL_DIR),
        "labeled": (labeled.load, EVAL_DIR),
        "products": (products.load, slices_dir),
        "mentions": (mentions.load, slices_dir),
        "metrics": (metrics.load, slices_dir),
    }
    out: dict[str, int] = {}
    with connect(url) as conn, conn.cursor() as cur:
        for name in GROUP_NAMES:
            if name in only:
                loader, source = loaders[name]
                out.update(loader(cur, source))
                # One transaction per group: needs_runtime carries a 60s transaction_timeout.
                conn.commit()
    return out
