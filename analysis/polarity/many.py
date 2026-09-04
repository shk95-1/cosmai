"""Default implementation of classify_many. Only an implementation with a batch API (#6's Claude) beats
looping over single calls.

The rule implementation inherits this class to fill the contract — the judgement logic stays in
analysis/polarity/__init__.py as it was (the comparison target is fixed) and only the widened signature is
filled in here.
"""

from __future__ import annotations

from collections.abc import Sequence

from analysis.types import AspectLexicon, PolarityRequest, PolarityResult


class SingleCallPolarity:
    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        raise NotImplementedError

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]
