"""One home for the LLM prompt — the Claude implementation and the ollama plumbing have to send the same
sentences for the difference between them to be a difference of models.

라벨 기준은 contracts/formats.md §라벨 기준(polarity) 그대로다: 그것이 골드의 정의이고, 프롬프트가
고쳐 쓰면 평가셋과 다른 과제를 채점하게 된다 (tests/test_llm_polarity.py 가 md 와 대조한다).
"""

from __future__ import annotations

from analysis.types import AspectLexicon

# Must be byte-identical to that line in contracts/formats.md.
LABEL_CRITERIA = (
    '작성자가 이 제품에서 겪은 부정 경험이 있으면 불만(약해도), "X 없음/적음"류 만족 표현은 만족, '
    "타제품·취향·피부타입 서술·잘린 문장·배송은 중립."
)
GENERIC = "(카테고리 무관)"
NO_CATEGORY = "(모름)"
NO_RATING = "(없음)"


def aspect_names(aspects: AspectLexicon) -> tuple[str, ...]:
    """Every name in the lexicon. Also the whitelist that filters out names the model invented."""
    found: list[str] = []
    for pattern in aspects.patterns:
        if pattern.aspect not in found:
            found.append(pattern.aspect)
    return tuple(found)


def aspect_menu(aspects: AspectLexicon) -> str:
    by_category: dict[str, list[str]] = {}
    for pattern in aspects.patterns:
        names = by_category.setdefault(pattern.category, [])
        if pattern.aspect not in names:
            names.append(pattern.aspect)
    # The category-less (generic) group goes last: a category-specific name hides the generic one (B5).
    ordered = sorted(by_category.items(), key=lambda item: (item[0] == "", item[0]))
    return "\n".join(f"  {category or GENERIC}: {', '.join(names)}" for category, names in ordered)


def system_prompt(aspects: AspectLexicon) -> str:
    return f"""너는 한국어 화장품 리뷰·댓글의 한 문장을 읽고 극성 하나를 판정한다.

# 라벨 기준 (polarity)
{LABEL_CRITERIA}

# 판정 순서
1. 그 문장이 말하는 것이 이 제품인가. 타제품·피부타입·취향 서술이면 중립이다.
2. 작성자가 이 제품에서 겪은 부정 경험이 하나라도 있으면 불만이다 — 약하게 적혀 있어도 불만이다.
3. "끈적임 없음"처럼 불만 항목이 없다/적다는 말은 만족이다.
4. 잘린 문장, 배송·포장 이야기, 판단이 서지 않는 문장은 중립이다.

# 출력
- polarity: 불만 | 만족 | 중립 중 하나. 이 셋 밖의 값은 없다.
- aspect: 아래 목록의 이름 하나. 어느 것도 해당하지 않으면 빈 문자열.
- reason: 그렇게 판정한 근거를 40자 이내 한국어로. 문장을 그대로 옮기지 않는다.

# aspect 목록 (카테고리: 이름)
{aspect_menu(aspects)}"""


def user_prompt(sentence: str, rating: float | None, category: str | None) -> str:
    return (
        f"카테고리: {category or NO_CATEGORY}\n"
        f"별점: {rating if rating is not None else NO_RATING}\n"
        f"문장: {sentence}"
    )
