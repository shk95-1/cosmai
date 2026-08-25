"""임베딩·색인에 넣을 청크와 그 계약 검증기 (slices/ydc/chunks.py).

계약은 다섯 칸이다: chunk_id(`{doc_id}#{ordinal}`) · doc_id · source · ordinal(0부터 연속) ·
text(normalize_text 를 거친 것). doc_id 가 소스 공통이라 소스를 합치는 일이 join 이 아니라
이어 붙이기가 된다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from analysis.retrieval.normalize import normalize_text

MAX_CHARS = 500  # multilingual-e5 의 512토큰 상한, 한국어 ~1.5자/토큰 -> 약 330토큰
FIELDS = ("chunk_id", "doc_id", "source", "ordinal", "text")
SAMPLES_PER_KIND = 3  # 같은 종류가 수만 건 나올 수 있다 -- 종류별 표본만 남겨야 보고서를 읽는다

# 자를 자리 우선순위. 문장 가운데를 자르면 그 청크만 뜻이 끊긴다.
BREAKS = (". ", "! ", "? ", "다. ", "요. ", "\n", " ")


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """limit 자 이하 조각으로. 겹침은 두지 않는다 -- 같은 문장이 여러 청크에 들어가면
    검색 상위가 한 문서로 채워지고, 근거는 여러 문서에서 모아야 한다."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    out: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = -1
        for mark in BREAKS:
            found = window.rfind(mark)
            # 너무 앞에서 자르면 조각이 잘게 부서진다. 절반은 넘겨야 한다.
            if found > limit // 2:
                cut = found + len(mark)
                break
        if cut <= 0:
            cut = limit  # 자를 자리가 없으면 그냥 끊는다
        piece = text[:cut].strip()
        if piece:
            out.append(piece)
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def problem_kind(message: str) -> str:
    """위반 메시지의 종류. 상한을 세는 단위이자 보고의 "N종"이 세는 단위다 -- 두 곳이 다른 규칙을
    쓰면 한쪽 상한만 걸리고 다른 쪽 숫자는 다른 것을 센다(#18 M12)."""
    return message.split(":")[0]


def row_ref(row: Mapping[str, object], line: int) -> str:
    """위반 메시지가 가리키는 좌표. 메시지는 사람이 원본을 찾아가라고 있는 것이므로 표의 기본키인
    chunk_id 를 먼저 쓴다 -- `WHERE chunk_id = ...` 한 문장이 그 행을 낸다. 못 만들면 (doc_id,
    ordinal) 인덱스, 그것도 없으면 훑은 순서뿐이다(그 번호는 부르는 쪽이 이어 세야 유일하다)."""
    chunk_id = str(row.get("chunk_id") or "").strip()
    if chunk_id:
        return chunk_id
    doc_id = str(row.get("doc_id") or "").strip()
    if doc_id:
        return f"doc_id={doc_id} ordinal={row.get('ordinal')!r}"
    source = str(row.get("source") or "").strip()
    return f"{line}행 (source={source})" if source else f"{line}행"


def check_rows(
    rows: Iterable[Mapping[str, object]], *, first_line: int = 2
) -> tuple[list[str], Counter, list[int], int]:
    """(위반 목록, 소스별 개수, 길이 목록, 문서 수). 파일을 읽지 않으므로 어디서든 검사한다.

    `first_line` 은 좌표가 아예 없는 행에 붙일 번호의 시작이다. 배치로 나눠 부르는 쪽
    (pipeline.run)이 이미 검사한 행 수만큼 밀어 주지 않으면 같은 번호가 배치마다 다시 나온다(#27).
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    ordinals: dict[str, list[int]] = defaultdict(list)
    per_source: Counter = Counter()
    lengths: list[int] = []

    def note(message: str) -> None:
        # 이 상한은 **한 번의 check_rows 안에서만** 걸린다. 실행 전체의 상한은 부르는 쪽이
        # 이어 세야 한다(pipeline.run) -- 배치마다 다시 부르면 여기서는 매번 0부터다.
        kind = problem_kind(message)
        if sum(1 for p in problems if problem_kind(p) == kind) < SAMPLES_PER_KIND:
            problems.append(message)

    for line, row in enumerate(rows, first_line):
        chunk_id = str(row.get("chunk_id") or "").strip()
        doc_id = str(row.get("doc_id") or "").strip()
        source = str(row.get("source") or "").strip()
        text = str(row.get("text") or "")
        ref = row_ref(row, line)

        if not chunk_id:
            note(f"chunk_id 없음: {ref}")
        elif chunk_id in seen_ids:
            note(f"chunk_id 중복: {ref}")
        seen_ids.add(chunk_id)

        if not doc_id:
            note(f"doc_id 없음: {ref}")
        if not source:
            note(f"source 없음: {ref}")
        if not text.strip():
            note(f"text 비어 있음: {ref}")
        # 정규화 규칙이 소스마다 갈리면 소스 간 점수 비교가 무의미해진다.
        if text != normalize_text(text):
            note(f"정규화 안 됨: {ref}")
        if len(text) > MAX_CHARS * 2:
            note(f"너무 긺: {ref} — {len(text)}자")

        try:
            ordinals[doc_id].append(int(row["ordinal"]))  # pyright: ignore[reportArgumentType]
        except (KeyError, TypeError, ValueError):
            note(f"ordinal 이 정수가 아님: {ref} — {row.get('ordinal')!r}")

        per_source[source] += 1
        lengths.append(len(text))

    for doc_id, values in ordinals.items():
        if sorted(values) != list(range(len(values))):
            note(f"ordinal 이 0 부터 연속이 아님: {doc_id} -> {sorted(values)[:6]}")
    return problems, per_source, lengths, len(ordinals)
