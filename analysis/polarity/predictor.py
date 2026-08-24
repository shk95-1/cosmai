"""`cosmai eval polarity --impl llm:<model>` 의 팩터리. registry.IMPLEMENTATIONS 가 import 한다.

한 실행이 두 사전(suncare-v2.2 · p1-v2.2)을 만나므로 시스템 프롬프트도 둘이다 — 사전별로 묶어
배치를 따로 낸다. 그래야 400문장이 프롬프트 캐시 두 개만 만들고, 섞여서 캐시가 매번 깨지지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from analysis.lexicon import load_aspects
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET, ruleset_for
from analysis.polarity.llm import LLMPolarity, version_for
from analysis.polarity.ollama import OllamaPolarity
from analysis.polarity.pricing import BudgetExceeded, UsageLedger
from analysis.predictors import category_of, connect_lexicon, rating_of
from analysis.registry import Implementation, LabeledRow, Predictor, register_factory
from analysis.types import AspectLexicon, Polarity, PolarityRequest, PolarityResult

IMPL_NAME = "llm"
OLLAMA_IMPL_NAME = "ollama"


def _rows_by_ruleset(rows: Sequence[LabeledRow]) -> tuple[list[PolarityRequest], dict[str, list[int]]]:
    by_ruleset: dict[str, list[int]] = {}
    items: list[PolarityRequest] = []
    for i, row in enumerate(rows):
        category = category_of(row)
        items.append(PolarityRequest(row.text, rating_of(row), category))
        by_ruleset.setdefault(ruleset_for(category), []).append(i)
    return items, by_ruleset


def _predictor(model: str) -> Predictor:
    def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
        items, by_ruleset = _rows_by_ruleset(rows)
        with connect_lexicon() as conn:
            aspects = {name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)}
            llm = LLMPolarity(model, UsageLedger(conn), purpose=f"eval:polarity:{version_for(model)}")
            out = [""] * len(rows)
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


def _ollama_predictor(model: str) -> Predictor:
    def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
        items, by_ruleset = _rows_by_ruleset(rows)
        out = [""] * len(rows)
        with connect_lexicon() as conn:
            # ollama 에는 배치 API 가 없어 문장마다 왕복한다(수 초, ollama.py:103) — 트랜잭션을 쥔 채
            # 그동안 기다리면 needs_runtime 의 idle_in_transaction_session_timeout(15s, db/bootstrap.sql)
            # 을 첫 문장에서 넘긴다(실측: IdleInTransactionSessionTimeout). llm 경로는 pricing.reserve()
            # 의 커밋이 부수적으로 이걸 막아 왔다 — reserve() 없는 무료 경로는 스스로 커밋해야 한다.
            conn.autocommit = True
            aspects = {name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)}
            ollama = OllamaPolarity(model, UsageLedger(conn))
            # 무료 경로는 reserve() 를 거치지 않으니 BudgetExceeded 가 날 수 없다 — llm 팩터리와
            # 달리 그 분기가 아예 없다(있으면 예산 보호가 있는 것처럼 오독된다).
            for ruleset, indexes in by_ruleset.items():
                found = ollama.classify_many([items[i] for i in indexes], aspects[ruleset])
                for i, result in zip(indexes, found, strict=True):
                    out[i] = result.polarity
        return out

    return predict


def build_ollama(model: str) -> Implementation:
    if not model:
        raise LookupError("--impl ollama:<model> needs a model, e.g. ollama:gemma4:latest")
    return Implementation(version=OllamaPolarity(model).version, predict=_ollama_predictor(model))


class _Blocking:
    """예산 하드스톱을 단계가 잡는 예외로 바꾼다 — BudgetExceeded(RuntimeError)는 analysis/pipeline.py 의
    FAILURES 밖이라 run 을 'running' 인 채 트레이스백으로 끝낸다."""

    def __init__(self, inner: Polarity) -> None:
        self.inner = inner
        self.version = inner.version

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return self.classify_many([PolarityRequest(sentence, rating, category)], aspects)[0]

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        try:
            return self.inner.classify_many(items, aspects)
        except BudgetExceeded as blocked:
            raise LookupError(str(blocked)) from blocked


@contextmanager
def open_llm(model: str) -> Iterator[Polarity]:
    """`analyze --impl llm:<model>`. 원장은 단계의 커넥션이 아니라 자기 것을 쓴다 — reserve() 의 커밋이
    단계의 미완성 upsert 를 같이 커밋해 버리면 페이지 단위 재개가 깨진다."""
    if not model:
        raise LookupError("--impl llm:<model> needs a model, e.g. llm:claude-sonnet-5")
    with connect_lexicon() as conn:
        yield _Blocking(
            LLMPolarity(model, UsageLedger(conn), purpose=f"analyze:polarity:{version_for(model)}")
        )


@contextmanager
def open_ollama(model: str) -> Iterator[Polarity]:
    """`analyze --impl ollama:<model>`. 무료 경로라 reserve() 의 커밋이 없다 — 그 커밋이 부수적으로
    막아 주던 idle_in_transaction 15s 를 autocommit 이 대신 막는다 (eval 쪽 f8aff76 과 같은 이유)."""
    if not model:
        raise LookupError("--impl ollama:<model> needs a model, e.g. ollama:gemma4:latest")
    with connect_lexicon() as conn:
        conn.autocommit = True
        yield OllamaPolarity(model, UsageLedger(conn))


register_factory("polarity", IMPL_NAME, build, paid=True, classifier=open_llm)
# 무료·로컬이라 --split 강제(cli.is_paid)에 걸리지 않는다 — 홀드아웃을 첫 호출로 바로 돌려도 된다.
register_factory("polarity", OLLAMA_IMPL_NAME, build_ollama, paid=False, classifier=open_ollama)
