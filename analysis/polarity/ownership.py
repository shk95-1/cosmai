"""어느 극성 구현이 어느 `lexicon_category` 를 소유하는가 — 이 파일 하나가 그 답이다 (#31).

005 의 자연키 `(src, ref, need_key, extractor_version, md5(sentence))` 에는 `polarity_version` 이
없다. 그래서 같은 추출기 버전으로 두 구현이 같은 문장을 처리하면 **행 단위 소유권이 성립하지 않는다** —
나중에 도는 쪽이 제자리 upsert 로 앞의 라벨을 덮고, 스코프 없는 실행의 삭제문은 남은 것마저 지운다.
소유는 그래서 행이 아니라 scope 단위다: 주인만 그 scope 를 쓰고 지우며, 주인이 없는 scope 는 지금처럼
누구든(=규칙) 갱신한다. `lexicon_category IS NULL` 인 행(댓글·카테고리 없는 리뷰)은 주인이 없다.

값은 그 구현이 산출 행에 찍는 `polarity_version` 그대로다 — 실행이 자기 것인지 아는 유일한 단서가
그 문자열이다. 판본이 오르면(few-shot·프롬프트 날짜) 이 표도 같이 옮겨야 하고,
tests/test_analyze_polarity.py 의 소유 표 검사가 그 순간을 잡는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["NO_OWNERS", "OWNERS", "foreign_scopes"]

# 2026-08-24 홀드아웃에서 gemma4 가 규칙을 넘었다 (interfaces.md §LLM 실측). 전 카테고리 패스는 ~40시간
# 이라 선블록부터 넘겼다 — 나머지 카테고리는 주인이 없어 규칙이 그대로 돈다.
OWNERS: Mapping[str, str] = MappingProxyType({"선블록": "llm-ollama-gemma4:latest-fs2-20260824"})
# 소유가 없던 시절의 동작 그대로 — 이 표를 비우면 규칙 실행이 다시 전량을 쓰고 지운다.
NO_OWNERS: Mapping[str, str] = MappingProxyType({})


def foreign_scopes(owners: Mapping[str, str], polarity_version: str) -> tuple[str, ...]:
    """이 판정자가 손대면 안 되는 scope — 다른 구현이 주인인 자리다."""
    return tuple(sorted(scope for scope, owner in owners.items() if owner != polarity_version))
