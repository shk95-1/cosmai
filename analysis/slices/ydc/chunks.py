#!/usr/bin/env python3
"""임베딩에 넣을 청크를 만들고, 남이 만든 청크가 계약을 지켰는지 검사한다.

**나누는 지점이 여기다.** 인코딩은 한 사람이 전체를 한 번에 돌린다(모델·프리픽스·
정규화가 하나만 어긋나도 벡터를 합칠 수 없다). 대신 "뭘 한 덩어리로 볼지"는 그
소스를 아는 사람만 정할 수 있으므로 소스별로 나눈다. 자세한 근거는
`reports/임베딩_검색_계획.md`.

계약. 이 다섯 칸이 전부다.

    chunk_id   전역 고유. `{doc_id}#{ordinal}` 로 만든다
    doc_id     5계층 공통 스키마의 그것. 이게 있어야 합치기가 join 이 아니라
               이어 붙이기가 된다
    source     document.csv 의 source 를 그대로. 하드코딩하지 않는다
    ordinal    문서 안에서 0 부터. 쪼개지 않은 문서는 0 하나
    text       trend.normalize_text 를 거친 본문

유튜브 규칙.

    댓글        1건 = 1청크. 중앙 길이가 짧아 쪼갤 게 없다
    영상 장문   제목 + 설명. 500자 넘으면 분할한다
    태그        넣지 않는다. `trend.py` 와 같은 결정이다 — 태그를 넣으면 모집단이
                964 -> 1,019편이 되고 모든 구성비가 움직인다

왜 500자인가. `multilingual-e5-base` 의 상한이 512토큰이고 한국어는 대략 글자
1.5개가 토큰 하나다. 500자면 약 330토큰이라 여유가 있다. 상한을 꽉 채우면 모델을
바꿀 때(bge-m3 등) 다시 쪼개야 한다.

`--validate` 는 남이 낸 파일을 검사한다. 인코딩 전에 걸러야 한다 — 26만 문서를
GPU 로 태운 다음 doc_id 가 어긋난 걸 알면 처음부터 다시다.

사용법:
    python chunks.py                                  # 유튜브 청크 생성
    python chunks.py --validate reports/chunks_상대.csv  # 남의 파일 검사
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from trend import normalize_text

csv.field_size_limit(10 ** 8)

MAX_CHARS = 500
FIELDS = ["chunk_id", "doc_id", "source", "ordinal", "text"]

# 자를 자리 우선순위. 문장 가운데를 자르면 그 청크만 뜻이 끊긴다.
BREAKS = [". ", "! ", "? ", "다. ", "요. ", "\n", " "]


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """limit 자 이하 조각으로. 가능한 문장 끝에서 자른다.

    겹침(overlap)을 두지 않는다. 경계에 걸친 문장을 놓칠 수 있지만, 겹치면 같은
    문장이 여러 청크에 들어가 검색 상위가 한 문서로 채워진다. 우리는 근거를
    여러 문서에서 모아야 하므로 중복이 더 해롭다.
    ponytail: 겹침 없음. 경계 재현율이 실제로 문제가 되면 20% 겹침을 넣는다
    """
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    out = []
    while len(text) > limit:
        window = text[:limit]
        cut = -1
        for mark in BREAKS:
            found = window.rfind(mark)
            # 너무 앞에서 자르면 조각이 잘게 부서진다. 절반은 넘겨야 한다
            if found > limit // 2:
                cut = found + len(mark)
                break
        if cut <= 0:
            cut = limit                     # 자를 자리가 없으면 그냥 끊는다
        piece = text[:cut].strip()
        if piece:
            out.append(piece)
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def build(common: Path, out: Path, sources: list[str] | None) -> int:
    rows = []
    per_source = Counter()
    split_docs = 0
    with (common / "document.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["quality_flags"]:
                continue                    # 빈 텍스트·중복은 인덱스에 넣지 않는다
            if sources and row["source"] not in sources:
                continue
            pieces = split_text(normalize_text(row["text"]))
            if not pieces:
                continue
            if len(pieces) > 1:
                split_docs += 1
            for ordinal, piece in enumerate(pieces):
                rows.append({
                    "chunk_id": f"{row['doc_id']}#{ordinal}",
                    "doc_id": row["doc_id"],
                    "source": row["source"],
                    "ordinal": ordinal,
                    "text": piece,
                })
            per_source[row["source"]] += len(pieces)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    docs = len({r["doc_id"] for r in rows})
    print(f"문서 {docs:,} -> 청크 {len(rows):,} (쪼갠 문서 {split_docs:,})")
    for source, n in per_source.most_common():
        print(f"  {source:<18}{n:>9,}")
    lengths = sorted(len(r["text"]) for r in rows)
    print(f"청크 길이 중앙 {lengths[len(lengths) // 2]}자 · 최대 {lengths[-1]}자")
    print(f"{out} 저장")
    return 0


def check_rows(rows: list[dict]) -> tuple[list[str], Counter, list[int], int]:
    """(위반 목록, 소스별 개수, 길이 목록, 문서 수). 파일을 읽지 않으므로 점검할 수 있다."""
    problems: list[str] = []
    seen_ids: set[str] = set()
    ordinals: dict[str, list[int]] = defaultdict(list)
    per_source = Counter()
    lengths = []

    def note(message: str) -> None:
        # 같은 종류가 수천 건 나올 수 있다. 종류별로 3건까지만 보여준다
        kind = message.split(":")[0]
        if sum(1 for p in problems if p.startswith(kind)) < 3:
            problems.append(message)

    for line, row in enumerate(rows, 2):
        chunk_id = (row.get("chunk_id") or "").strip()
        doc_id = (row.get("doc_id") or "").strip()
        source = (row.get("source") or "").strip()
        text = row.get("text") or ""

        if not chunk_id:
            note(f"chunk_id 없음: {line}행")
        elif chunk_id in seen_ids:
            note(f"chunk_id 중복: {line}행 {chunk_id}")
        seen_ids.add(chunk_id)

        if not doc_id:
            note(f"doc_id 없음: {line}행")
        if not source:
            note(f"source 없음: {line}행")
        if not text.strip():
            note(f"text 비어 있음: {line}행 {chunk_id}")
        # 정규화 규칙이 갈리면 소스 간 비교가 무의미해진다. 팀 합의 사항이다
        if text != normalize_text(text):
            note(f"정규화 안 됨: {line}행 {chunk_id}")
        if len(text) > MAX_CHARS * 2:
            note(f"너무 긺: {line}행 {chunk_id} — {len(text)}자")

        try:
            ordinals[doc_id].append(int(row["ordinal"]))
        except (TypeError, ValueError):
            note(f"ordinal 이 정수가 아님: {line}행 {row.get('ordinal')!r}")

        per_source[source] += 1
        lengths.append(len(text))

    for doc_id, values in ordinals.items():
        if sorted(values) != list(range(len(values))):
            note(f"ordinal 이 0 부터 연속이 아님: {doc_id} -> {sorted(values)[:6]}")
    return problems, per_source, lengths, len(ordinals)


def validate(path: Path) -> int:
    """계약 위반을 전부 모아서 낸다. 첫 오류에서 멈추면 왕복이 늘어난다."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [f for f in FIELDS if f not in (reader.fieldnames or [])]
        if missing:
            print(f"[실패] 컬럼이 없다: {', '.join(missing)}")
            print(f"필요한 컬럼: {', '.join(FIELDS)}")
            return 1
        rows = list(reader)

    problems, per_source, lengths, docs = check_rows(rows)

    print(f"{path} — 청크 {len(rows):,} · 문서 {docs:,}")
    for source, n in per_source.most_common():
        print(f"  {source:<18}{n:>9,}")
    if lengths:
        lengths.sort()
        print(f"청크 길이 중앙 {lengths[len(lengths) // 2]}자 · 최대 {lengths[-1]}자")
    print()
    if problems:
        print(f"[실패] 계약 위반 {len(problems)}종")
        for p in problems:
            print(f"  {p}")
        return 1
    print("[통과] 계약을 지켰다. 인코딩에 넣어도 된다")
    return 0


def demo() -> None:
    assert split_text("") == []
    assert split_text("짧은 글") == ["짧은 글"]
    # 문장 끝에서 자른다
    body = "가" * 300 + ". " + "나" * 300
    pieces = split_text(body, 400)
    assert len(pieces) == 2 and pieces[0].endswith("."), pieces[0][-20:]
    # 자를 자리가 아예 없으면 그냥 끊는다. 예외를 던지면 파이프라인이 멈춘다
    assert split_text("가" * 1200, 500) == ["가" * 500, "가" * 500, "가" * 200]
    # 조각이 상한을 넘지 않아야 한다 — 모델 입력 한계를 넘으면 뒤가 잘린다
    for limit in (100, 300, 500):
        for piece in split_text(("문장입니다. " * 200), limit):
            assert len(piece) <= limit, (limit, len(piece))
    # 절반 앞에서는 자르지 않는다. 잘게 부서지면 청크 수만 늘고 뜻이 없다
    assert len(split_text("가. " + "나" * 400, 300)) == 2
    # 내용이 보존돼야 한다. 공백만 달라진다
    joined = "".join(split_text("문장 하나. 문장 둘. 문장 셋.", 12)).replace(" ", "")
    assert joined == "문장하나.문장둘.문장셋."

    # 검증기가 실제로 위반을 잡는지. 통과만 하는 검증기는 장식이다
    def row(**kw):
        base = {"chunk_id": "d#0", "doc_id": "d", "source": "s",
                "ordinal": "0", "text": "백탁 없이 촉촉"}
        return {**base, **kw}

    assert not check_rows([row()])[0], "정상 행을 통과시켜야 한다"
    assert not check_rows([row(), row(chunk_id="d#1", ordinal="1")])[0]

    def caught(*rows):
        return bool(check_rows(list(rows))[0])

    assert caught(row(chunk_id=""))                       # id 없음
    assert caught(row(), row())                           # id 중복
    assert caught(row(doc_id=""))                         # doc_id 없음
    assert caught(row(source=""))                         # source 없음
    assert caught(row(text="   "))                        # 빈 본문
    assert caught(row(text="백탁   없이"))                 # 정규화 안 됨(공백 축약)
    assert caught(row(text="&lt;백탁&gt;"))                # 정규화 안 됨(엔티티)
    assert caught(row(ordinal="첫째"))                     # 정수 아님
    assert caught(row(ordinal="1"))                       # 0 부터 시작 안 함
    assert caught(row(), row(chunk_id="d#2", ordinal="2"))  # 연속 아님
    assert caught(row(text="가" * (MAX_CHARS * 2 + 1)))    # 너무 긺
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--common", type=Path, default=Path("common"))
    p.add_argument("--out", type=Path, default=Path("reports/chunks_youtube.csv"))
    p.add_argument("--source", action="append",
                   help="youtube_video / youtube_comment 등. 없으면 전부")
    p.add_argument("--validate", type=Path, help="남이 만든 chunks.csv 를 검사한다")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if a.validate:
        return validate(a.validate)
    return build(a.common, a.out, a.source)


if __name__ == "__main__":
    raise SystemExit(main())
