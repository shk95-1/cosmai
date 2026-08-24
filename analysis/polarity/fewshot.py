"""ollama 전용 few-shot 예시. Claude 경로는 이 파일을 import 하지 않는다.

gemma4 를 `think:false` 로 돌리면 9.7배 빨라지지만 규칙(acc .870) 아래로 떨어진다 — 사고 토큰이 하던
일을 예시가 대신한다. 예시는 튠 셋에서만 뜬다: 홀드아웃 문장이 여기 섞이면 남은 블라인드 1회가
튜닝 점수가 된다 (tests/test_ollama_polarity.py 가 두 홀드아웃 CSV 와 대조한다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET

# 예시가 바뀌면 이 태그가 바뀐다 — ollama 버전 문자열이 곧 프롬프트 판본이다 (contracts/versioning.md).
FEWSHOT_TAG = "fs0"


@dataclass(frozen=True)
class Shot:
    """튠 CSV 한 행 그대로. source 는 그 행의 i 열이라 어느 행을 베꼈는지 되짚을 수 있다."""

    source: str
    sentence: str
    rating: float | None
    category: str | None
    aspect: str
    polarity: str
    reason: str


SHOTS: dict[str, tuple[Shot, ...]] = {SUNCARE_RULESET: (), GENERIC_RULESET: ()}


def shots_for(ruleset: str) -> list[dict[str, Any]]:
    """예시를 지난 대화로 넣는다 — 시스템 프롬프트에 붙이면 스키마 밖 서술이 되어 형식이 흔들린다."""
    from analysis.polarity.prompt import user_prompt

    out: list[dict[str, Any]] = []
    for shot in SHOTS.get(ruleset, ()):
        answer = {"aspect": shot.aspect, "polarity": shot.polarity, "reason": shot.reason}
        out.append({"role": "user", "content": user_prompt(shot.sentence, shot.rating, shot.category)})
        out.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    return out
