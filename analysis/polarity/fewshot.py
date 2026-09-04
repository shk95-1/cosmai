"""Few-shot examples for ollama only. The Claude path does not import this file.

Running gemma4 with `think:false` is 9.7x faster but drops below the rules (acc .870) — the examples take
over the work the thinking tokens were doing. The examples come from the tune set only: a holdout sentence
mixed in here would turn the one blind run that is left into a tuning score (tests/test_ollama_polarity.py
compares them against the two holdout CSVs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET

# This tag changes when the examples change — the ollama version string is the prompt revision
# (contracts/versioning.md).
FEWSHOT_TAG = "fs2"


@dataclass(frozen=True)
class Shot:
    """One row of the tune CSV as it stands. source is that row's i column, so the copied row is traceable."""

    source: str
    sentence: str
    rating: float | None
    category: str | None
    aspect: str
    polarity: str
    reason: str


SUN = "선블록"
# 고른 기준은 1회차(fs0) 튠 오답의 모양이다: 오답 51건 중 30건이 불만→중립, 11건이 만족→중립이었다.
# 그래서 완곡한 불만·약한 만족을 각각 세우고, 그 반대편(잘린 문장·남에게 좋다는 말)을 같은 수로
# 세워 불만 정밀도가 딸려 내려가지 않게 한다.
SHOTS: dict[str, tuple[Shot, ...]] = {
    SUNCARE_RULESET: (
        Shot(
            "suncare_tune200 i=10",
            "저처럼 극 건성이신 분이라면 신중히 구입하시길 바래요",
            2.0,
            SUN,
            "",
            "불만",
            "구입에 주의를 권하는 것은 겪은 부정 경험이다",
        ),
        Shot(
            "suncare_tune200 i=120",
            "솔직히 요즘 손에 많이 가지는 않는제품이예요",
            3.0,
            SUN,
            "",
            "불만",
            "손이 안 간다는 말은 약하게 적힌 불만이다",
        ),
        Shot(
            "suncare_tune200 i=76",
            "단점을 굳이 뽑으라고한다면 제형이 제법 묽고 잘 발리는 제형인데 튜브가 너무나 쉽게 짜이는 "
            "재질이라 조심하는 게 좋다는 정도?",
            5.0,
            SUN,
            "발림텍스처",
            "불만",
            "별점이 높아도 단점을 말하면 불만이다",
        ),
        Shot(
            "suncare_tune200 i=176",
            "전체적으로 나쁘지 않은 선택이었습니다",
            3.0,
            SUN,
            "",
            "만족",
            "약한 긍정도 만족이다",
        ),
        Shot(
            "suncare_tune200 i=189",
            "가격도 괜찮고 구성도 넉넉하네요^^",
            5.0,
            SUN,
            "용량가격",
            "만족",
            "가격과 구성을 긍정한다",
        ),
        Shot(
            "suncare_tune200 i=106",
            "지성 피부 기준으로는 살짝 유분감이 있는데 건성 피부가 쓰면 딱 좋을 것 같아요.",
            4.0,
            SUN,
            "끈적유분",
            "불만",
            "다른 타입엔 좋아도 내 피부엔 단점이 있다",
        ),
        Shot(
            "suncare_tune200 i=113",
            "되직한 느낌???",
            3.0,
            SUN,
            "",
            "중립",
            "잘린 문장이라 판단이 서지 않는다",
        ),
        Shot("suncare_tune200 i=174", "모든 톤이 다", 2.0, SUN, "", "중립", "문장이 중간에서 잘렸다"),
        Shot(
            "suncare_tune200 i=53",
            "건성분들은 너무 잘 사용하실듯 !!",
            2.0,
            SUN,
            "",
            "중립",
            "단점 없이 다른 타입에 좋다는 말뿐이다",
        ),
    ),
    GENERIC_RULESET: (
        Shot(
            "crosscat_60 i=2",
            "뜯겨져 나가고 위에 포장지도 누가 벗긴 것 처럼 돼서왔어요",
            1.0,
            "클렌징워터",
            "배송포장",
            "불만",
            "받은 물건이 훼손돼 있었다",
        ),
        Shot(
            "crosscat_60 i=46",
            "좋은 제품이라고 생각은 하는데 저랑 너무 안맞는 제품이여서요",
            1.0,
            "BB/CC",
            "기타불만",
            "불만",
            "칭찬을 붙여도 안 맞았다는 것이 겪은 경험이다",
        ),
        Shot(
            "crosscat_60 i=43",
            "본격적으로/데일리로 바르긴 무서워서 가끔 두피괄사 할 때 코땃쥐만큼씩 사용 중이에요.",
            2.0,
            "헤어토닉/앰플",
            "자극따가움",
            "불만",
            "무서워서 아껴 쓴다는 것은 불만이다",
        ),
        Shot(
            "crosscat_60 i=53",
            "수부지 타입에는 이제품이 딱인거 같습니다",
            3.0,
            "에센스",
            "",
            "만족",
            "자기 피부에 맞았다는 말이다",
        ),
        Shot(
            "crosscat_60 i=37",
            "받지도않았는데 받었다고하는군요",
            1.0,
            "BB/CC",
            "배송포장",
            "불만",
            "주문·배송이 잘못된 것도 겪은 불만이다",
        ),
        Shot(
            "crosscat_60 i=36",
            "덜빠질까봐 계속써요ㅠㅠ여자탈모는 너무 괴로워요ㅠㅠ",
            5.0,
            "헤어토닉/앰플",
            "탈모비듬",
            "만족",
            "계속 쓴다는 것은 효과를 인정한 말이다",
        ),
        Shot(
            "crosscat_60 i=44",
            "나름 발림성은 좋은 것 같은데 다시 사봐서 써봐야할 것 같움 ㅠ",
            1.0,
            "쿠션",
            "",
            "중립",
            "아직 판단을 미룬 문장이다",
        ),
    ),
}


def shots_for(ruleset: str) -> list[dict[str, Any]]:
    """The examples go in as earlier turns — attached to the system prompt they become prose outside the
    schema and the output format wobbles."""
    from analysis.polarity.prompt import user_prompt

    out: list[dict[str, Any]] = []
    for shot in SHOTS.get(ruleset, ()):
        answer = {"aspect": shot.aspect, "polarity": shot.polarity, "reason": shot.reason}
        out.append({"role": "user", "content": user_prompt(shot.sentence, shot.rating, shot.category)})
        out.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    return out
