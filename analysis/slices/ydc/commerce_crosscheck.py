#!/usr/bin/env python3
"""유튜브 트렌드 판정을 커머스 플랫폼 속성 평가와 대조한다.

왜 필요한가. 우리 판정은 전부 언급량에서 나왔다. 언급량으로 만든 지표를 언급량으로
검증하면 순환이다. 커머스 DB 의 `review_topic` 은 올리브영·다이소가 **자체 리뷰 설문으로
집계한 속성 평가**라서, 언급량과 독립된 유일한 검증 재료다.

무엇을 비교하나. 값이 아니라 **방향**을 본다. 두 지표는 분모가 다르다.
  우리   composition = 그 주제 언급 문서 수 / 그 분기 전체 주제 언급 문서 수  (주제 간 구성비)
  커머스 share_pct  = 그 선택지 응답 비중 / topic_group 내 합 100            (제품 내 응답 분포)
분모가 다른 값을 나란히 놓고 크기를 비교하면 틀린다. 그래서 우리 쪽은 주제 간 순위,
커머스 쪽은 긍정률로 바꿔 놓고 해석한다.

주의 — 커머스 데이터의 두 가지 성질을 전제로 한다.
  1. `review_topic` 의 관측 창은 며칠뿐이다(2026-08-18~21). 분기 추세를 만들 길이가
     아니므로 우리의 최근 확정 분기(2026Q2)와 현재 상태를 대조하는 데만 쓴다.
  2. `review_topic` 은 시간별 스냅샷이다. 같은 (제품, 선택지)가 수집 시점마다 한 행씩
     쌓여 있다(현재 28개 시점, 7.2배). 중복 적재가 아니라 설계상 시계열이며
     (제품, 선택지, captured_at) 조합에 진짜 중복은 0행이다.
     다만 속성 평가는 리뷰가 쌓여야 바뀌므로 시점 간 값이 거의 같다. 집계할 때는
     제품·선택지별 **최신 시점 한 행만** 쓴다. 전부 세면 제품 수가 시점 수만큼 부풀려진다.

사용법:
    python commerce_crosscheck.py --api http://100.106.220.24:3000
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import urllib.request
from collections import defaultdict
from pathlib import Path

from topics import TOPICS

PAGE = 1000                     # PostgREST max-rows
MIN_PRODUCTS = 5                # 우리 판정 기준(document_count >= 5)을 대조에도 같이 적용한다
SUN_TERMS = [k.lower() for k in next(t for t in TOPICS if t["topic"] == "선크림")["ko"]]

# 커머스 topic_group -> 우리 topic_id. 대응이 없는 것은 넣지 않는다.
GROUP_MAP = {
    "자극도": "자극_눈시림",
    "보습력": "촉촉함_건조함",
    "수분감": "촉촉함_건조함",
    "지속력": "지속력_워터프루프",
    "발림성": "발림성",
    "커버력": "톤업_메이크업베이스",
    "발색력": "톤업_메이크업베이스",
}

# topic_name 의 극성. 커머스는 선택지 문구로 긍정/중립/부정을 나타낸다.
NEGATIVE_HINTS = ("느껴져요", "아쉬", "부족", "무거", "끈적", "밀려", "answer_no", "없어요")
NEUTRAL_HINTS = ("보통",)

FIELDS = ["topic_id", "commerce_groups", "products_rated", "positive_rate_mean",
          "positive_rate_median", "youtube_rank_comment", "youtube_composition_pct",
          "youtube_gap_pp", "youtube_trend_type", "reading"]


def fetch_all(api: str, table: str, select: str = "*") -> list[dict]:
    """PostgREST 는 max-rows 를 걸어 두므로 offset 으로 끝까지 받는다."""
    out: list[dict] = []
    while True:
        url = f"{api}/{table}?select={select}&limit={PAGE}&offset={len(out)}"
        with urllib.request.urlopen(url, timeout=60) as res:
            page = json.load(res)
        out.extend(page)
        if len(page) < PAGE:
            return out


def polarity(topic_name: str) -> str:
    name = (topic_name or "").strip()
    if any(h in name for h in NEUTRAL_HINTS):
        return "neutral"
    if any(h in name for h in NEGATIVE_HINTS):
        return "negative"
    return "positive"


def positive_rate(choices: list[tuple[str, float]]) -> float | None:
    """한 제품·한 topic_group 안에서 긍정 선택지가 차지하는 비중."""
    total = sum(share for _, share in choices)
    if not total:
        return None
    pos = sum(share for name, share in choices if polarity(name) == "positive")
    return 100 * pos / total


def load_youtube(path: Path, quarter: str) -> dict[str, dict]:
    rows = [r for r in csv.DictReader(path.open(encoding="utf-8-sig"))
            if r["quarter"] == quarter]
    comment = {r["topic_id"]: r for r in rows if r["source"] == "youtube_comment"}
    ranked = sorted((r for r in comment.values() if r["composition"]),
                    key=lambda r: -float(r["composition"]))
    rank = {r["topic_id"]: i + 1 for i, r in enumerate(ranked)}
    for t, r in comment.items():
        r["_rank"] = rank.get(t)
    return comment


def run(api: str, judgement: Path, quarter: str, out: Path) -> None:
    products = fetch_all(api, "product", "source,product_key,name")
    sun = {(p["source"], p["product_key"]) for p in products
           if any(t in (p["name"] or "").lower() for t in SUN_TERMS)}
    print(f"제품 {len(products):,}개 중 선크림 관련 {len(sun):,}개")

    raw = fetch_all(api, "review_topic",
                    "source,product_key,topic_group,topic_name,share_pct,captured_at")
    # 시간별 스냅샷이므로 (제품, 선택지)별 최신 시점만 남긴다. API 반환 순서에
    # 기대지 않도록 captured_at 으로 명시해 고른다.
    latest: dict[tuple, dict] = {}
    for r in raw:
        key = (r["source"], r["product_key"], r["topic_group"], r["topic_name"])
        cur = latest.get(key)
        if cur is None or (r.get("captured_at") or "") > (cur.get("captured_at") or ""):
            latest[key] = r
    deduped = list(latest.values())
    stamps = {r.get("captured_at") for r in raw}
    print(f"review_topic {len(raw):,}행 = {len(deduped):,}개 (제품,선택지) x {len(stamps)}개 시점"
          f" -> 최신 시점만 {len(deduped):,}행 사용")

    # (제품, topic_group) -> [(선택지, 비중)]
    grouped: dict[tuple, list[tuple[str, float]]] = defaultdict(list)
    for r in deduped:
        if (r["source"], r["product_key"]) not in sun:
            continue
        if r["topic_group"] not in GROUP_MAP or r["share_pct"] is None:
            continue
        grouped[(r["source"], r["product_key"], r["topic_group"])].append(
            (r["topic_name"], float(r["share_pct"])))

    per_topic: dict[str, list[float]] = defaultdict(list)
    groups_used: dict[str, set] = defaultdict(set)
    for (src, key, group), choices in grouped.items():
        rate = positive_rate(choices)
        if rate is None:
            continue
        per_topic[GROUP_MAP[group]].append(rate)
        groups_used[GROUP_MAP[group]].add(group)

    yt = load_youtube(judgement, quarter)
    rows = []
    for topic, rates in sorted(per_topic.items(), key=lambda kv: -len(kv[1])):
        y = yt.get(topic, {})
        comp = float(y.get("composition") or 0) * 100
        gap = float(y.get("gap_pp") or 0)
        mean = statistics.mean(rates)
        rows.append({
            "topic_id": topic,
            "commerce_groups": "|".join(sorted(groups_used[topic])),
            "products_rated": len(rates),
            "positive_rate_mean": round(mean, 1),
            "positive_rate_median": round(statistics.median(rates), 1),
            "youtube_rank_comment": y.get("_rank"),
            "youtube_composition_pct": round(comp, 2),
            "youtube_gap_pp": round(gap, 2),
            "youtube_trend_type": y.get("trend_type"),
            "reading": reading(mean, gap),
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print()
    print(f"{'주제':<16}{'제품수':>5}{'커머스 긍정률':>13}{'유튜브순위':>9}"
          f"{'구성비%':>9}{'gap%p':>8}  해석")
    for r in rows:
        print(f"{r['topic_id']:<16}{r['products_rated']:>5}"
              f"{r['positive_rate_mean']:>12.1f}%{str(r['youtube_rank_comment']):>9}"
              f"{r['youtube_composition_pct']:>9.2f}{r['youtube_gap_pp']:>8.2f}  {r['reading']}")
    thin = [r for r in rows if r["products_rated"] < MIN_PRODUCTS]
    if thin:
        print()
        print(f"[근거 부족] 제품 {MIN_PRODUCTS}개 미만인 주제 {len(thin)}개 — 해석을 쓰지 않는다.")
        print("  우리 판정에 document_count >= 5 를 요구하면서 이 대조에만 예외를 두면 이중 기준이다.")
        print("  커머스 쪽 선크림 커버리지가 올라가면 같은 명령으로 다시 돌린다.")
    print()
    print(f"{out} 저장")


def reading(pos_rate: float, gap: float) -> str:
    """언급이 많은데 만족도가 낮으면 개선 여지, 둘 다 높으면 이미 해결된 강점."""
    if gap > 1.0 and pos_rate < 80:
        return "소비자 불만 실재 · 제품 공백 근거 강화"
    if gap > 1.0 and pos_rate >= 80:
        return "많이 말하지만 만족도 높음 · 공백이 아니라 관심"
    if pos_rate < 80:
        return "만족도 낮음 · 언급은 적어 관찰 필요"
    return "만족도 높고 갭 작음 · 포화"


def demo() -> None:
    assert polarity("자극없이 순해요") == "positive"
    assert polarity("자극이 느껴져요") == "negative"
    assert polarity("보통이에요") == "neutral"
    # 자극도 실측: 순해요 70 / 보통 29 / 느껴져요 1 -> 긍정 70%
    assert abs(positive_rate([("자극없이 순해요", 70), ("보통이에요", 29),
                              ("자극이 느껴져요", 1)]) - 70.0) < 1e-9
    assert positive_rate([]) is None
    assert positive_rate([("보통이에요", 100)]) == 0.0, "중립만 있으면 긍정 0"
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://100.106.220.24:3000")
    p.add_argument("--judgement", type=Path,
                   default=Path("reports/trend_judgement_v0.2.csv"))
    p.add_argument("--quarter", default="2026Q2", help="확정된 최근 분기")
    p.add_argument("--out", type=Path, default=Path("reports/commerce_crosscheck.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    run(a.api, a.judgement, a.quarter, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
