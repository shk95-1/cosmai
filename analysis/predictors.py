"""The predictors of unit #3 that registry.load_implementations() plugs in: polarity · wish_class.

The Predictor contract (interfaces.md) takes batch rows only and no connection -- the dictionary is read by
the predictor itself. LEXICON_URL is the one place that points that connection somewhere other than
needs_runtime: `cosmai eval --url` and the tests both come in here. **Every registered predictor has to go
through this one place** -- a predictor holding its own URL is out of reach of --url and that predictor alone
reads the production DB quietly (the linker family did).

connect_lexicon · category_of · rating_of are used by the LLM predictor of #6 and the linker predictor of #4
as well.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import psycopg

from analysis.extractor import RuleExtractor
from analysis.lexicon import load_aspects, load_lexicon
from analysis.polarity import GENERIC_RULESET, SUNCARE_CATEGORY, SUNCARE_RULESET, RulePolarity, ruleset_for
from analysis.registry import LabeledRow, register
from analysis.types import TextUnit

LEXICON_URL: str | None = None
# The wish rules do not look at time, but TextUnit requires an observation date.
EVAL_DAY = date(2026, 8, 23)


def connect_lexicon() -> psycopg.Connection[Any]:
    from db.runtime import runtime_url
    from db.seed._common import connect

    return connect(LEXICON_URL or runtime_url())


def set_lexicon_url(url: str | None) -> None:
    """The `cosmai eval --url` hook: it sends the dictionary connection to the same DB (or back to the
    production fallback without --url) so that one eval does not straddle two DBs."""
    global LEXICON_URL
    LEXICON_URL = url


def category_of(row: LabeledRow) -> str | None:
    named = row.extra.get("category")
    if isinstance(named, str) and named:
        return named
    # The sun set is one suncare review category, so it has no category column (eval/polarity/suncare_*.csv).
    return SUNCARE_CATEGORY if row.ref.startswith("sun:") else None


def rating_of(row: LabeledRow) -> float | None:
    rating = row.extra.get("rating")
    return float(rating) if isinstance(rating, str | int | float) and str(rating) else None


def predict_polarity(rows: Sequence[LabeledRow]) -> Sequence[str]:
    rule = RulePolarity()
    with connect_lexicon() as conn:
        aspects = {name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)}
    out = []
    for row in rows:
        category = category_of(row)
        out.append(rule.classify(row.text, rating_of(row), category, aspects[ruleset_for(category)]).polarity)
    return out


def predict_wish_class(rows: Sequence[LabeledRow]) -> Sequence[str]:
    extractor = RuleExtractor()
    with connect_lexicon() as conn:
        lexicon = load_lexicon(conn)
    out = []
    for row in rows:
        unit = TextUnit(
            src="yt_comment",
            site="youtube",
            ref=row.ref,
            text=row.text,
            observed_at=EVAL_DAY,
            observed_at_resolution="month",
        )
        found = extractor.wishes(unit, lexicon)
        # Only a|b|c are stored, but the scoring is 4-class including n (the gold has n in it).
        out.append(found.wish_class if found else "n")
    return out


def register_implementations() -> None:
    """Only registry.load_implementations() causes registration -- called at module level, whoever imports
    this module first decides the registry state (#99)."""
    register("polarity", RulePolarity.version, predict_polarity)
    register("wish_class", RuleExtractor.version, predict_wish_class)
