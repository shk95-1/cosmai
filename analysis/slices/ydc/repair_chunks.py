#!/usr/bin/env python3
"""남이 만든 청크 파일을 계약에 맞게 고친다. `chunks.py --validate` 의 짝이다.

왜 필요한가. 수호님 성분·식약처 청크(8,786행)가 계약을 두 곳에서 어겼다.
생성기 쪽에서 고치는 게 근본이지만 발표까지 이틀이라, **후처리로 막고 생성기
수정은 따로 부탁하는 병행**을 택했다. 이 파일이 그 후처리다.

고치는 것 두 가지.

1. **정규화 누락 (215행)** — 제품명에 있던 이중 공백이 그대로 들어갔다.
   `제품 [광노화 선 케어]  싸이닉` 처럼 대괄호 뒤가 두 칸이다. 사소해 보이지만
   같은 제품이 검색에서 다른 토큰열이 된다. `trend.normalize_text` 를 통과시킨다.

2. **doc_id 충돌 (제품 577개 전부)** — `formula_summary` 와 `formula_full` 이 같은
   `doc_id`(`PROD:제품명`)를 쓴다. 그래서 한 doc_id 안에 ordinal 이 [0, 0, 1, 2] 로
   들어간다. 계약(0부터 연속)을 깨는 것보다 더 나쁜 건 **근거 추적이 깨지는 것**이다 —
   doc_id 로 되짚었을 때 요약인지 전성분인지 구분할 수 없다.
   `source` 를 prefix 로 넣어 갈라 준다: `PROD:x` → `PROD_SUMMARY:x` / `PROD_FULL:x`.

`chunk_id` 는 `{doc_id}#{ordinal}` 로 다시 만든다. 원본은 `C8c7c40aebb8f945b` 같은
해시라 사람이 읽을 수 없고, doc_id 를 바꾸면 어차피 다시 만들어야 한다.

**본문은 정규화 외에 건드리지 않는다.** 내용 판단은 그 소스를 아는 사람 몫이다.
성분명에 섞인 잡음(`000ppm함유` 같은 것)도 그대로 둔다 — 여기서 지우면 그가
생성기를 고칠 때 무엇이 문제였는지 알 수 없다.

사용법:
    python repair_chunks.py 받은파일.csv --out reports/chunks_ingredient_mfds.csv
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from chunks import FIELDS, check_rows
from trend import normalize_text

csv.field_size_limit(10 ** 8)

# 같은 doc_id 를 쓰면 안 되는 소스 짝. 값은 doc_id 앞에 붙일 접미사다.
SPLIT_NAMESPACE = {"formula_summary": "_SUMMARY", "formula_full": "_FULL"}


def namespaced(doc_id: str, source: str) -> str:
    """소스가 다르면 doc_id 도 달라야 한다. `PROD:x` -> `PROD_SUMMARY:x`"""
    suffix = SPLIT_NAMESPACE.get(source)
    if not suffix or ":" not in doc_id:
        return doc_id
    head, rest = doc_id.split(":", 1)
    return f"{head}{suffix}:{rest}"


def repair(rows: list[dict]) -> tuple[list[dict], Counter]:
    """계약에 맞게 고친 행과, 무엇을 몇 개 고쳤는지."""
    fixed_rows: list[dict] = []
    counted = Counter()
    per_doc: dict[str, int] = defaultdict(int)

    for row in rows:
        text = row.get("text") or ""
        clean = normalize_text(text)
        if clean != text:
            counted["정규화"] += 1

        source = (row.get("source") or "").strip()
        doc_id = namespaced((row.get("doc_id") or "").strip(), source)
        if doc_id != (row.get("doc_id") or "").strip():
            counted["doc_id 분리"] += 1

        # ordinal 을 doc_id 안에서 다시 매긴다. 원래 값이 겹쳐 있으므로 신뢰할 수 없다
        ordinal = per_doc[doc_id]
        per_doc[doc_id] += 1
        if str(ordinal) != (row.get("ordinal") or ""):
            counted["ordinal 재부여"] += 1

        fixed_rows.append({"chunk_id": f"{doc_id}#{ordinal}", "doc_id": doc_id,
                           "source": source, "ordinal": ordinal, "text": clean})
    return fixed_rows, counted


def run(source: Path, out: Path) -> int:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    before, _p, _l, _d = check_rows(rows)
    print(f"{source.name} — {len(rows):,}행 · 들어온 상태 위반 {len(before)}종")
    for p in before:
        print(f"    {p}")

    fixed, counted = repair(rows)
    print()
    print("고친 것")
    for what, n in counted.most_common():
        print(f"    {what:<14}{n:>7,}행")

    problems, per_source, lengths, docs = check_rows(fixed)
    print()
    print(f"고친 뒤 — 청크 {len(fixed):,} · 문서 {docs:,}")
    for name, n in per_source.most_common():
        print(f"    {name:<18}{n:>7,}")
    lengths.sort()
    print(f"길이 중앙 {lengths[len(lengths) // 2]}자 · 최대 {lengths[-1]}자")
    print()
    if problems:
        print(f"[실패] 아직 위반 {len(problems)}종 — 본문 판단이 필요한 것일 수 있다")
        for p in problems:
            print(f"    {p}")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(fixed)
    print(f"[통과] {out} 저장")
    return 0


def demo() -> None:
    assert namespaced("PROD:가나", "formula_summary") == "PROD_SUMMARY:가나"
    assert namespaced("PROD:가나", "formula_full") == "PROD_FULL:가나"
    # 갈라야 할 소스가 아니면 그대로 둔다
    assert namespaced("ING:판테놀", "ingredient") == "ING:판테놀"
    assert namespaced("MFDS:123", "mfds") == "MFDS:123"
    # 콜론이 없으면 손대지 않는다
    assert namespaced("이상한id", "formula_full") == "이상한id"

    rows = [
        {"chunk_id": "Cx", "doc_id": "PROD:가", "source": "formula_summary",
         "ordinal": "0", "text": "제품  가"},                    # 공백 2개
        {"chunk_id": "Cy", "doc_id": "PROD:가", "source": "formula_full",
         "ordinal": "0", "text": "전성분 1.정제수"},              # doc_id 충돌
        {"chunk_id": "Cz", "doc_id": "PROD:가", "source": "formula_full",
         "ordinal": "1", "text": "2.글리세린"},
    ]
    fixed, counted = repair(rows)
    ids = [r["doc_id"] for r in fixed]
    assert ids == ["PROD_SUMMARY:가", "PROD_FULL:가", "PROD_FULL:가"], ids
    assert [r["ordinal"] for r in fixed] == [0, 0, 1]
    assert fixed[0]["text"] == "제품 가" and counted["정규화"] == 1
    assert fixed[0]["chunk_id"] == "PROD_SUMMARY:가#0"
    # 고친 결과는 계약을 통과해야 한다
    assert not check_rows(fixed)[0], check_rows(fixed)[0]
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", type=Path, nargs="?")
    p.add_argument("--out", type=Path,
                   default=Path("reports/chunks_ingredient_mfds.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if not a.source:
        p.error("고칠 파일을 주거나 --demo 를 쓴다")
    return run(a.source, a.out)


if __name__ == "__main__":
    raise SystemExit(main())
