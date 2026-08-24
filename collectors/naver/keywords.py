"""Reads `keywords.json` -- the one file that names which categories and complaint-keyword groups
this collector asks Naver about (contracts/formats.md: keywords.json is not `needs.entity_lexicon`,
a second usage point is what would justify a dictionary table for this)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

KEYWORDS_PATH = Path(__file__).resolve().parent / "keywords.json"


class Query(NamedTuple):
    """One blog-search query: a single term out of one category's one group."""

    category: str
    group_key: str
    term: str


def load(path: Path = KEYWORDS_PATH) -> dict[str, dict[str, tuple[str, ...]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        category: {group: tuple(terms) for group, terms in groups.items()}
        for category, groups in raw.items()
        if not category.startswith("_")
    }


def groups(category: str, path: Path = KEYWORDS_PATH) -> dict[str, tuple[str, ...]]:
    """A category's groups, in the exact shape a DataLab `keywordGroups` request wants:
    `{group_key: terms}`."""
    return load(path).get(category, {})


def queries(path: Path = KEYWORDS_PATH) -> list[Query]:
    """Every (category, group, term) triple -- what `blog` walks, one query per term (the blog
    search endpoint takes one `query` string per call, unlike DataLab's grouped keywords)."""
    return [
        Query(category, group, term)
        for category, group_map in load(path).items()
        for group, terms in group_map.items()
        for term in terms
    ]


__all__ = ["Query", "load", "groups", "queries", "KEYWORDS_PATH"]
