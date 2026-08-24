"""registry.IMPLEMENTATIONS 가 import 하는 유닛 #3 의 예측자: polarity · wish_class.

Predictor 계약(interfaces.md)은 배치 행만 받고 연결은 받지 않는다 — 사전은 스스로 읽는다.
LEXICON_URL 은 그 연결을 needs_runtime 이 아닌 곳으로 돌리는 유일한 자리다: `cosmai eval --url` 도,
테스트도 여기로 들어온다. **등록된 모든 예측자가 이 한 자리를 거쳐야 한다** — 예측자가 자기 URL 을
따로 들면 --url 이 닿지 않아 그 예측자만 조용히 운영 DB 를 읽는다(linker 계열이 그랬다).

connect_lexicon·category_of·rating_of 는 #6 의 LLM 예측자와 #4 의 linker 예측자도 쓴다.
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
# 바람 규칙은 시각을 보지 않지만 TextUnit 은 관측일을 요구한다.
EVAL_DAY = date(2026, 8, 23)


def connect_lexicon() -> psycopg.Connection[Any]:
    from db.runtime import runtime_url
    from db.seed._common import connect

    return connect(LEXICON_URL or runtime_url())


def set_lexicon_url(url: str | None) -> None:
    """`cosmai eval --url` 훅: 사전 접속도 같은 DB 로 보내(또는 --url 없으면 운영 폴백으로 되돌려)
    한 번의 eval 이 두 DB 에 걸치지 않게 한다."""
    global LEXICON_URL
    LEXICON_URL = url


def category_of(row: LabeledRow) -> str | None:
    named = row.extra.get("category")
    if isinstance(named, str) and named:
        return named
    # sun 셋은 선케어 리뷰 한 카테고리라 카테고리 열이 없다 (eval/polarity/suncare_*.csv).
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
        # 저장 대상은 a|b|c 뿐이지만 채점 대상은 n 을 포함한 4클래스다 (골드에 n 이 있다).
        out.append(found.wish_class if found else "n")
    return out


register("polarity", RulePolarity.version, predict_polarity)
register("wish_class", RuleExtractor.version, predict_wish_class)
