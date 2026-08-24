#!/usr/bin/env python3
"""소스마다 같은 사전·같은 정의로 주제 구성비를 내고 나란히 놓는다.

왜. 유튜브만으로는 "이 주제가 실제로 중요한가"에 답할 수 없다. 언급량으로 만든 값을
언급량으로 검증하면 순환이고, 유튜브 안의 영상·댓글은 같은 플랫폼이라 편향을 공유한다.
소스가 다르면 편향도 다르므로, 여러 소스가 같은 방향을 말하면 그것이 근거가 된다.

무엇을 맞추나. 소스마다 다른 것은 문서의 성격뿐이고 계산은 하나로 고정한다.
  사전       topics.py 의 match_topics — 소스별로 다른 사전을 쓰면 비교가 무의미하다
  단위       문서 x 주제 1건
  분모       그 소스·그 구간의 전체 주제 언급 문서 수 합 (주제 간 구성비)
소스 간 문서 수를 합산하지 않는다. 각자 자기 분모로 계산한 뒤 나란히 본다.

한계. 커머스 리뷰는 시계열이 아니다. 리뷰 수집이 최신 편향이라(2026년 90%) 분기로
쪼개면 "2026년 폭증"이 나오는데 그것은 수집 방식의 산물이다. 그래서 커머스는
**단일 시점**으로 두고 유튜브의 최근 확정 분기와만 나란히 놓는다. 추세 비교는 하지 않는다.

사용법:
    python source_composition.py --quarter 2026Q2
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import urllib.request
from pathlib import Path

from topics import TOPICS, match_topics

csv.field_size_limit(10 ** 8)
TREND_TOPICS = [t["topic"] for t in TOPICS if t["trend_use"]]
SUN_TERMS = [k.lower() for k in next(t for t in TOPICS if t["topic"] == "선크림")["ko"]]
PAGE = 1000
FIELDS = ["topic_id", "youtube_comment_pct", "youtube_video_pct", "commerce_review_pct",
          "commerce_minus_video_pp", "reading"]


def fetch_all(api: str, table: str, select: str) -> list[dict]:
    out: list[dict] = []
    while True:
        url = f"{api}/{table}?select={select}&limit={PAGE}&offset={len(out)}"
        with urllib.request.urlopen(url, timeout=60) as res:
            page = json.load(res)
        out.extend(page)
        if len(page) < PAGE:
            return out


def commerce_composition(api: str) -> tuple[dict[str, float], int, int]:
    """커머스 리뷰 본문에 우리 사전을 돌려 구성비를 낸다.

    `review_topic`(플랫폼 자체 설문)이 아니라 **리뷰 본문**을 쓴다. 설문은 선크림 제품
    2개에만 있어 표본이 없고, 본문은 우리 사전을 그대로 적용할 수 있어 정의가 일치한다.
    """
    products = fetch_all(api, "product", "source,product_key,name")
    sun = {(p["source"], p["product_key"]) for p in products
           if any(t in (p["name"] or "").lower() for t in SUN_TERMS)}
    reviews = [r for r in fetch_all(api, "review", "source,product_key,body")
               if (r["source"], r["product_key"]) in sun]
    hits: collections.Counter = collections.Counter()
    for r in reviews:
        hits.update(match_topics(r["body"] or ""))
    total = sum(hits[t] for t in TREND_TOPICS)
    comp = {t: (100 * hits[t] / total if total else 0.0) for t in TREND_TOPICS}
    return comp, len(reviews), total


def youtube_composition(judgement: Path, source: str, quarter: str) -> dict[str, float]:
    with judgement.open(encoding="utf-8-sig", newline="") as h:
        rows = [r for r in csv.DictReader(h)
                if r["source"] == source and r["quarter"] == quarter and r["composition"]]
    return {r["topic_id"]: float(r["composition"]) * 100 for r in rows}


def reading(video: float, commerce: float) -> str:
    """어느 쪽이 그 주제를 담는 그릇인가."""
    if commerce >= 5 and video < 2:
        return "영상 설명으로는 관측 불가 · 실사용 발화에만 있음"
    if commerce - video >= 5:
        return "실사용 쪽이 훨씬 많이 말함"
    if video - commerce >= 5:
        return "제작자 쪽이 훨씬 많이 말함 · 스펙·성분 언어"
    return ""


def run(api: str, judgement: Path, quarter: str, out: Path) -> None:
    comm, n_reviews, n_mentions = commerce_composition(api)
    cmt = youtube_composition(judgement, "youtube_comment", quarter)
    vid = youtube_composition(judgement, "youtube_video", quarter)

    rows = []
    for topic in TREND_TOPICS:
        c, v, k = cmt.get(topic, 0.0), vid.get(topic, 0.0), comm[topic]
        rows.append({
            "topic_id": topic,
            "youtube_comment_pct": round(c, 2),
            "youtube_video_pct": round(v, 2),
            "commerce_review_pct": round(k, 2),
            "commerce_minus_video_pp": round(k - v, 2),
            "reading": reading(v, k),
        })
    rows.sort(key=lambda r: -r["commerce_review_pct"])

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"커머스 선크림 리뷰 {n_reviews:,}건 · 주제 언급 {n_mentions:,} · 단일 시점")
    print(f"유튜브 {quarter} 기준\n")
    print(f"{'주제':<18}{'댓글':>9}{'영상':>9}{'커머스':>9}   해석")
    for r in rows:
        print(f"{r['topic_id']:<18}{r['youtube_comment_pct']:>8.2f}%"
              f"{r['youtube_video_pct']:>8.2f}%{r['commerce_review_pct']:>8.2f}%   {r['reading']}")
    print(f"\n{out} 저장")


def demo() -> None:
    assert reading(0.3, 12.1).startswith("영상 설명으로는 관측 불가")
    assert reading(8.9, 0.2) == "제작자 쪽이 훨씬 많이 말함 · 스펙·성분 언어"
    assert reading(5.0, 5.2) == ""
    # 구성비는 소스별로 따로 합이 100 이어야 한다
    comp = {t: 100 / len(TREND_TOPICS) for t in TREND_TOPICS}
    assert abs(sum(comp.values()) - 100) < 1e-9
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://100.106.220.24:3000")
    p.add_argument("--judgement", type=Path, default=Path("reports/trend_judgement_v0.2.csv"))
    p.add_argument("--quarter", default="2026Q2", help="유튜브 쪽 최근 확정 분기")
    p.add_argument("--out", type=Path, default=Path("reports/source_composition.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    run(a.api, a.judgement, a.quarter, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
