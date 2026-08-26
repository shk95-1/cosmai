"""근거 없는 질의를 막는다 (포크 #48, ydc `vector_threshold.py` 의 `df_gate`).

**골격이다 -- 규칙은 다음 커밋이 넣는다.** 계약 §벡터 하한선 이 재기 전에 정한 판정으로 코사인
하한선을 버렸고(진짜 질의와 가짜 성분명의 분포가 갈리지 않는다), 그 자리에 남는 축이 문서빈도다.
"""

from __future__ import annotations

from dataclasses import dataclass

ZERO_DF_MINLEN = 4  # df 0 을 "코퍼스에 없는 이름" 의 근거로 볼 최소 토큰 길이. 실측이 정한다


@dataclass(frozen=True)
class Grounding:
    """질의가 코퍼스에 근거를 갖는가. `note` 는 통과든 차단이든 사람에게 그 이유를 말한다."""

    ok: bool
    note: str
    missing: tuple[str, ...] = ()


def check(query: str, index) -> Grounding:
    """이 질의를 검색해도 되는가."""
    raise NotImplementedError("포크 #48 이 규칙을 넣는다")
