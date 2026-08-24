"""`cosmai eval polarity --impl llm:<model>` 의 팩터리. registry.IMPLEMENTATIONS 가 import 한다.

한 실행이 두 사전(suncare-v2.2 · p1-v2.2)을 만나므로 시스템 프롬프트도 둘이다 — 사전별로 묶어
배치를 따로 낸다. 그래야 400문장이 프롬프트 캐시 두 개만 만들고, 섞여서 캐시가 매번 깨지지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence

from analysis.lexicon import load_aspects
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET, ruleset_for
from analysis.polarity.llm import LLMPolarity, version_for
from analysis.polarity.pricing import BudgetExceeded, UsageLedger
from analysis.predictors import category_of, connect_lexicon, rating_of
from analysis.registry import Implementation, LabeledRow, Predictor, register_factory
from analysis.types import PolarityRequest

IMPL_NAME = "llm"


def _predictor(model: str) -> Predictor:
    def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
        by_ruleset: dict[str, list[int]] = {}
        items: list[PolarityRequest] = []
        for i, row in enumerate(rows):
            category = category_of(row)
            items.append(PolarityRequest(row.text, rating_of(row), category))
            by_ruleset.setdefault(ruleset_for(category), []).append(i)
        out = [""] * len(rows)
        with connect_lexicon() as conn:
            aspects = {name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)}
            llm = LLMPolarity(model, UsageLedger(conn), purpose=f"eval:polarity:{version_for(model)}")
            try:
                for ruleset, indexes in by_ruleset.items():
                    found = llm.classify_many([items[i] for i in indexes], aspects[ruleset])
                    for i, result in zip(indexes, found, strict=True):
                        out[i] = result.polarity
            # 예산 차단은 blocked(exit 2)여야 한다 — cli 의 그 경로가 LookupError 를 잡는다.
            except BudgetExceeded as blocked:
                raise LookupError(str(blocked)) from blocked
        return out

    return predict


def build(model: str) -> Implementation:
    if not model:
        raise LookupError("--impl llm:<model> needs a model, e.g. llm:claude-sonnet-5")
    return Implementation(version=version_for(model), predict=_predictor(model))


register_factory("polarity", IMPL_NAME, build, paid=True)
