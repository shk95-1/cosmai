"""Text normalization. The same surface form however many sources are added (normalize_text in
slices/ydc/trend.py)."""

from __future__ import annotations

import html
import re
import unicodedata

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE_RE = re.compile(r"\s+")


# Fixed-point iteration cap. Measured, it settles after 2 rounds (double escaping, `&amp;lt;`, is the worst).
# The cap is there to stop an adversarial input (`&amp;amp;amp;...`) from dragging the loop out.
_MAX_ROUNDS = 4


def _once(text: str) -> str:
    # 제어문자는 공백이 아니라 없앤다: 공백으로 바꾸면 `백\x00탁` 이 `백 탁` 이 되어
    # 부분문자열 사전이 그 주제를 놓친다.
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    return WHITESPACE_RE.sub(" ", CONTROL_RE.sub("", text)).strip()


def normalize_text(value: str | None) -> str:
    """HTML entities unescaped -> NFKC -> control characters removed -> whitespace collapsed. **Run to the
    fixed point.**

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
