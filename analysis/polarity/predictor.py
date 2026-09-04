"""Factory for `cosmai eval polarity --impl llm:<model>`. registry.load_implementations() plugs it in.

One run meets two lexicons (suncare-v2.2 · p1-v2.2), so there are two system prompts as well — the rows are
grouped per lexicon and sent as separate batches. That way 400 sentences build only two prompt caches instead
of breaking the cache on every mixed call.
"""

from __future__ import annotations

import http.client
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
            # A budget stop has to be blocked (exit 2) — that path in the cli catches LookupError.
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
            # ollama has no batch API, so every sentence is a round trip (seconds, ollama.py:103) — waiting
            # that out while holding a transaction passes needs_runtime's
            # idle_in_transaction_session_timeout (15s, db/bootstrap.sql) on the first sentence (measured:
            # IdleInTransactionSessionTimeout). On the llm path the commit in pricing.reserve() has been
            # stopping this as a side effect — the free path, which has no reserve(), must commit for itself.
            conn.autocommit = True
            aspects = {name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)}
            ollama = OllamaPolarity(model, UsageLedger(conn))
            # The free path never goes through reserve(), so BudgetExceeded cannot be raised — unlike the llm
            # factory there is no such branch at all (one would be misread as budget protection in place).
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


# Exactly the surface of a broken round trip: urlopen raises URLError · TimeoutError (both OSError) and
# getresponse() lets RemoteDisconnected · IncompleteRead through as they are. It must not be widened here —
# swallowing a programming mistake such as AttributeError as well closes the run quietly as failed and hides
# the bug in a single note line.
UNREACHABLE = (OSError, http.client.HTTPException)


class _Blocking:
    """Turns whatever stops this classifier into an exception the stage catches — neither the budget hard stop
    (BudgetExceeded, RuntimeError) nor a failed round trip (URLError and friends, OSError) is inside
    FAILURES in
    analysis/pipeline.py, so letting either through ends the stage in a traceback and leaves the run polarity
    opened sitting at 'running' forever."""

    def __init__(self, inner: Polarity) -> None:
        self.inner = inner
        self.version = inner.version

    def preflight(self) -> None:
        # The stage looks it up by this name — a probe on the wrapped classifier is invisible unless it is
        # exposed here.
        probe = getattr(self.inner, "preflight", None)
        if probe is None:
            return
        try:
            probe()
        except UNREACHABLE as unreachable:
            raise LookupError(f"{type(unreachable).__name__}: {unreachable}") from unreachable

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return self.classify_many([PolarityRequest(sentence, rating, category)], aspects)[0]

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        try:
            return self.inner.classify_many(items, aspects)
        except BudgetExceeded as blocked:
            raise LookupError(str(blocked)) from blocked
        except UNREACHABLE as unreachable:
            raise LookupError(f"{type(unreachable).__name__}: {unreachable}") from unreachable


@contextmanager
def open_llm(model: str) -> Iterator[Polarity]:
    """`analyze --impl llm:<model>`. The ledger uses its own connection, not the stage's — if the commit in
    reserve() committed the stage's half-finished upsert with it, resuming page by page would break."""
    if not model:
        raise LookupError("--impl llm:<model> needs a model, e.g. llm:claude-sonnet-5")
    with connect_lexicon() as conn:
        yield _Blocking(
            LLMPolarity(model, UsageLedger(conn), purpose=f"analyze:polarity:{version_for(model)}")
        )


@contextmanager
def open_ollama(model: str) -> Iterator[Polarity]:
    """`analyze --impl ollama:<model>`. autocommit makes "a round trip is not waited out inside a transaction"
    a property of this connection — the only reason it is safe today is the accident that record() commits for
    itself, and eval died in the same place (f8aff76) because loading the lexicon held a transaction open."""
    if not model:
        raise LookupError("--impl ollama:<model> needs a model, e.g. ollama:gemma4:latest")
    with connect_lexicon() as conn:
        conn.autocommit = True
        yield _Blocking(OllamaPolarity(model, UsageLedger(conn)))


def register_implementations() -> None:
    """Only registry.load_implementations() causes registration (#99)."""
    register_factory("polarity", IMPL_NAME, build, paid=True, classifier=open_llm)
    # Free and local, so the forced --split (cli.is_paid) does not apply — a holdout can run on call one.
    register_factory("polarity", OLLAMA_IMPL_NAME, build_ollama, paid=False, classifier=open_ollama)
