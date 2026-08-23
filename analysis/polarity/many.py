"""classify_many 의 기본 구현. 배치 API 를 가진 구현(#6 의 Claude)만 단건 반복보다 낫다.

규칙 구현이 이 클래스를 상속해 계약을 채운다 — 판정 로직은 analysis/polarity/__init__.py 에 그대로 두고
(비교 대상 고정), 늘어난 시그니처만 여기서 메운다.
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
