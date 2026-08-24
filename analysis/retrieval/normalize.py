"""텍스트 정규화. 소스가 늘어도 같은 표면형을 만든다 (slices/ydc/trend.py normalize_text)."""

from __future__ import annotations

import html
import re
import unicodedata

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE_RE = re.compile(r"\s+")


# 고정점 반복 상한. 실측으로 2회면 멎는다(이중 이스케이프 `&amp;lt;`가 최악). 상한을 두는
# 이유는 적대적 입력(`&amp;amp;amp;...`)이 루프를 길게 끄는 것을 막기 위해서다.
_MAX_ROUNDS = 4


def _once(text: str) -> str:
    # 제어문자는 공백이 아니라 없앤다: 공백으로 바꾸면 `백\x00탁` 이 `백 탁` 이 되어
    # 부분문자열 사전이 그 주제를 놓친다.
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    return WHITESPACE_RE.sub(" ", CONTROL_RE.sub("", text)).strip()


def normalize_text(value: str | None) -> str:
    """HTML 엔티티 해제 -> NFKC -> 제어문자 제거 -> 공백 축약. **고정점까지 돌린다.**

    한 번만 돌리면 멱등이 아니다 -- `&amp;lt;` 는 한 번에 `&lt;` 까지만 풀린다. 그런데
    chunks.check_rows 가 `text != normalize_text(text)` 로 계약 위반을 판정하므로, 한 번만
    정규화해 저장한 청크가 영구히 "정규화 안 됨" 으로 잡힌다. ydc 원본은 수집기가
    `textFormat=plainText` 로 받아 이 경우가 없었지만, 우리 코퍼스는 DB 의 댓글·리뷰라
    이중 이스케이프가 실제로 들어온다.
    """
    text = value or ""
    for _ in range(_MAX_ROUNDS):
        folded = _once(text)
        if folded == text:
            return text
        text = folded
    return text
