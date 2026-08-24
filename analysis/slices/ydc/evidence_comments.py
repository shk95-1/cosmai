#!/usr/bin/env python3
"""주제별 근거 댓글 선별. (A5) — R&D Opportunity Card 에 붙일 실제 발화.

수집은 `order=relevance`로 받았는데 그건 좋아요 순이 아니다(유튜브 비공개 알고리즘).
저장된 `like_count`로 다시 정렬하면 재수집 없이 좋아요 상위를 뽑을 수 있다.

모집단은 판정과 같다 — `panel_role=product` 34채널의 장문 영상 중 선크림을 언급한
것에 달린 댓글. 여기서 벗어나면 카드의 근거와 지표의 모집단이 달라진다.

사용법:
    python evidence_comments.py --quarter 2026Q2 --top 3
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10 ** 8)

FIELDS = ["topic_id", "quarter", "rank", "like_count", "doc_id", "video_id",
          "channel_id", "matched_term", "url", "text"]


def author_hash(channel_id: str) -> str:
    """수집기가 댓글 작성자 채널 ID를 해시한 것과 같은 규칙(youtube_collector.py)."""
    return hashlib.sha256(f"youtube:{channel_id}".encode("utf-8")).hexdigest()[:24]


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def quarter_of(published_at: str) -> str:
    year, month = published_at[:4], int(published_at[5:7])
    return f"{year}Q{(month - 1) // 3 + 1}"


def collect(common: Path, quarter: str | None, top: int) -> list[dict]:
    product = {r["channel_id"] for r in read_csv(common / "channel.csv")
               if r["panel_role"] == "product"}

    topics_of = defaultdict(set)
    terms_of: dict[tuple[str, str], str] = {}
    for m in read_csv(common / "mention.csv"):
        topics_of[m["doc_id"]].add(m["topic_id"])
        terms_of[(m["doc_id"], m["topic_id"])] = m["matched_term"]

    # 1차 통과: 모집단 영상 확정 (선크림 언급 + 장문 + product)
    video_quarter: dict[str, str] = {}
    comments: list[dict] = []
    for d in read_csv(common / "document.csv"):
        if d["source"] == "youtube_video":
            if (d["content_type"] == "video_long" and d["channel_id"] in product
                    and "선크림" in topics_of.get(d["doc_id"], ())):
                video_quarter[d["source_item_id"]] = quarter_of(d["published_at"])
        elif d["quality_flags"] == "":            # 빈 댓글·복붙은 근거로 쓰지 않는다
            comments.append(d)

    rows = []
    skipped: dict[str, int] = defaultdict(int)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in comments:
        q = video_quarter.get(c["parent_item_id"])
        if not q or (quarter and q != quarter):
            continue
        try:
            meta = json.loads(c["source_metadata"])
            likes = int(meta.get("like_count") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            meta, likes = {}, 0
        # 제작자 본인 댓글은 근거로 쓰지 않는다. 좋아요 상위가 대부분 고정 댓글
        # (타임라인·인사말·AI 요약)이라 소비자 발화가 아니다. 작성자 해시가 그 영상
        # 채널의 해시와 같으면 본인이다.
        if meta.get("author_channel_hash") == author_hash(c["channel_id"]):
            skipped["creator"] += 1
            continue
        for topic in topics_of.get(c["doc_id"], ()):
            buckets[(topic, q)].append({
                "topic_id": topic, "quarter": q, "like_count": likes,
                "doc_id": c["doc_id"], "video_id": c["parent_item_id"],
                "channel_id": c["channel_id"],
                "matched_term": terms_of.get((c["doc_id"], topic), ""),
                "url": c["url"], "text": c["text"][:300],
            })

    for (topic, q), items in sorted(buckets.items()):
        items.sort(key=lambda r: -r["like_count"])
        for rank, item in enumerate(items[:top], 1):
            rows.append({**item, "rank": rank})
    if skipped:
        print("제외: " + ", ".join(f"{k} {v:,}건" for k, v in skipped.items()))
    return rows


def demo() -> None:
    assert quarter_of("2026-07-01T00:00:00Z") == "2026Q3"
    assert quarter_of("2026-01-31T12:00:00Z") == "2026Q1"
    assert quarter_of("2023-12-31T23:59:59Z") == "2023Q4"
    assert quarter_of("2024-04-01T00:00:00Z") == "2024Q2"
    # 해시 규칙이 수집기와 같아야 제작자 댓글이 걸린다
    assert author_hash("UCabc") == author_hash("UCabc")
    assert author_hash("UCabc") != author_hash("UCxyz")
    assert len(author_hash("UCabc")) == 24
    print("demo ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--common", type=Path, default=Path("common"))
    parser.add_argument("--quarter", help="예: 2026Q2. 생략하면 전 분기")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("reports/evidence_comments.csv"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo:
        demo()
        return 0

    rows = collect(args.common, args.quarter, args.top)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    topics = len({r["topic_id"] for r in rows})
    print(f"{args.out} : {len(rows)}행, 주제 {topics}개"
          + (f", {args.quarter}" if args.quarter else ", 전 분기"))
    for r in rows[:5]:
        print(f"   [{r['topic_id']}] 좋아요 {r['like_count']:,} · {r['text'][:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
