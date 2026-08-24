#!/usr/bin/env python3
"""커머스 리뷰 본문을 청크로 만든다. `chunks.py` 의 커머스 쪽 짝이다.

**왜 이게 급한가.** 임베딩의 값은 "이름 없는 불만을 찾는 것"이다. 그런데 그 불만이
가장 많이 적혀 있는 곳이 커머스 리뷰 본문인데, 지금까지 청크에 없었다 —
유튜브 278,916 + 성분·식약처 8,786 뿐이었다.

현준님이 재 준 숫자가 이걸 드러냈다. `review_topic`(14,232행)은 사이트가 제공한
**객관식 보기**다 — `topic_name` 이 "복합성에 좋아요" 같은 것이고 `sentence` 는
전부 비어 있다. 행 수가 392/392/392, 301/301/301 로 정확히 같은 것도 그 증거다.
체크박스지 발화가 아니다.

진짜 불만은 자유 텍스트에만 있다. 실측(2026-08-24, 리뷰 19,863건):

    백탁 552 · 따가 265 · 눈시림 252 · 하얗게 80 · 겉돌 61
    (review_topic 쪽에는 전부 0)

우리가 "커머스 백탁 12.09%" 를 찾은 이유가 이것이다. 객관식만 보면 백탁은
존재하지 않는 문제다.

추출은 `commerce_ranking.py` 와 같은 규칙을 쓴다 — **정지 + 전순서 정렬 + 행수
대조.** 정렬 없이 offset 페이징을 하다 중복 37% 를 만든 적이 있다.

한 리뷰 = 한 청크다. 500자를 넘으면 나눈다(긴 리뷰가 있다).

사용법:
    python commerce_chunks.py
    python chunks.py --validate reports/chunks_commerce.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from chunks import FIELDS, check_rows, split_text
from trend import normalize_text

csv.field_size_limit(10 ** 8)

BASE = "http://100.106.220.24:3000"
PAGE = 1000
COLUMNS = "source,review_key,product_key,rating,body,written_at,captured_at"
# 전순서 정렬. 이 조합이 유일하지 않으면 페이징이 흔들린다
ORDER = "captured_at.asc,source.asc,review_key.asc"


def fetch(freeze: str, cache: Path, offline: bool) -> list[dict]:
    """리뷰 전량. 정지·정렬·행수 대조를 건다(`commerce_ranking.fetch` 와 같은 이유)."""
    if cache.exists() and offline:
        rows = json.loads(cache.read_text(encoding="utf-8"))
        print(f"캐시 {len(rows):,}행 (--offline)")
        return rows
    if offline:
        raise SystemExit(f"캐시가 없다: {cache}")

    where = f"captured_at=lt.{urllib.parse.quote(freeze)}"
    head = urllib.request.Request(
        f"{BASE}/review?{where}&select=review_key",
        headers={"Accept": "application/json", "Prefer": "count=exact",
                 "Range": "0-0"})
    with urllib.request.urlopen(head, timeout=60) as handle:
        expected = int(handle.headers["Content-Range"].split("/")[1])

    rows, offset = [], 0
    while True:
        url = (f"{BASE}/review?{where}&select={COLUMNS}&order={ORDER}"
               f"&limit={PAGE}&offset={offset}")
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as handle:
            batch = json.load(handle)
        if not batch:
            break
        rows.extend(batch)
        offset += PAGE

    if len(rows) != expected:
        raise SystemExit(
            f"행수가 어긋난다 — 서버 {expected:,} vs 받은 것 {len(rows):,}. "
            f"페이징 중에 집합이 변했다. freeze 를 더 과거로 잡아야 한다.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"정지 시각 {freeze} 이전 {len(rows):,}행 (서버 행수와 일치) → {cache}")
    return rows


def build(rows: list[dict]) -> list[dict]:
    """한 리뷰 = 한 청크. doc_id 는 `commerce_review:{소스}:{리뷰키}`.

    `product_key` 는 본문에 넣지 않는다. 소스마다 체계가 달라 그대로 넣으면
    검색에 잡음만 된다 — 제품 연결은 `entity_link` 계층이 할 일이다.
    별점은 넣는다. "별 1개인데 촉촉하다고 함" 같은 걸 사람이 볼 수 있어야 한다.
    """
    out = []
    seen: set[str] = set()
    for row in rows:
        body = normalize_text(row.get("body"))
        if not body:
            continue
        source, key = row.get("source") or "?", row.get("review_key") or ""
        doc_id = f"commerce_review:{source}:{key}"
        if doc_id in seen:      # 같은 리뷰가 여러 스냅샷에 잡힐 수 있다
            continue
        seen.add(doc_id)
        rating = row.get("rating")
        head = f"[{source} 리뷰" + (f" 별점 {rating}]" if rating else "]")
        for ordinal, piece in enumerate(split_text(f"{head} {body}")):
            out.append({"chunk_id": f"{doc_id}#{ordinal}", "doc_id": doc_id,
                        "source": "commerce_review", "ordinal": ordinal,
                        "text": piece})
    return out


def run(freeze: str, cache: Path, out: Path, offline: bool) -> int:
    rows = fetch(freeze, cache, offline)
    chunks = build(rows)

    docs = len({c["doc_id"] for c in chunks})
    print(f"리뷰 {len(rows):,} -> 문서 {docs:,} · 청크 {len(chunks):,}")
    print(f"  본문 비어 중복 제외 {len(rows) - docs:,}")
    per = Counter(r.get("source") for r in rows)
    for name, n in per.most_common():
        print(f"  {name:<14}{n:>8,}")
    lengths = sorted(len(c["text"]) for c in chunks)
    print(f"청크 길이 중앙 {lengths[len(lengths) // 2]}자 · "
          f"평균 {statistics.fmean(lengths):.0f}자 · 최대 {lengths[-1]}자")

    # 임베딩이 왜 필요한지 이 파일로 보여줄 수 있어야 한다. 사전에 없는 표현이
    # 여기 얼마나 있는지 같이 낸다
    print()
    joined = [c["text"] for c in chunks]
    for word in ("백탁", "하얗게", "눈시림", "따가", "겉돌", "촉촉"):
        n = sum(1 for t in joined if word in t)
        print(f"  '{word}' 가 든 청크 {n:>6,}")

    problems, _p, _l, _d = check_rows(chunks)
    print()
    if problems:
        print(f"[실패] 계약 위반 {len(problems)}종")
        for p in problems:
            print(f"    {p}")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(chunks)
    print(f"[통과] chunks.py 계약을 지켰다 — {out} 저장")
    return 0


def demo() -> None:
    rows = [
        {"source": "oliveyoung", "review_key": "r1", "rating": 5,
         "body": "백탁  없이 촉촉해요"},                       # 공백 2개
        {"source": "hwahae", "review_key": "r2", "rating": None,
         "body": "눈이 시려요"},
        {"source": "oliveyoung", "review_key": "r1", "rating": 5,
         "body": "백탁 없이 촉촉해요"},                        # 같은 리뷰 재수집
        {"source": "daisomall", "review_key": "r3", "rating": 1, "body": "  "},
    ]
    got = build(rows)
    ids = [c["doc_id"] for c in got]
    assert ids == ["commerce_review:oliveyoung:r1", "commerce_review:hwahae:r2"], ids
    # 정규화가 걸려야 한다
    assert "백탁 없이 촉촉해요" in got[0]["text"], got[0]["text"]
    # 별점은 본문에 남기고, 없으면 표시하지 않는다
    assert "별점 5" in got[0]["text"] and "별점" not in got[1]["text"]
    # 빈 본문은 청크를 만들지 않는다
    assert not any("daisomall" in c["doc_id"] for c in got)
    assert not check_rows(got)[0], check_rows(got)[0]
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--freeze", default="2026-08-24T00:00:00+00:00")
    p.add_argument("--cache", type=Path, default=Path(".cache/review.json"))
    p.add_argument("--out", type=Path, default=Path("reports/chunks_commerce.csv"))
    p.add_argument("--offline", action="store_true")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    return run(a.freeze, a.cache, a.out, a.offline)


if __name__ == "__main__":
    raise SystemExit(main())
