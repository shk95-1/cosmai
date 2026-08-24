#!/usr/bin/env python3
"""전성분표로 선크림을 무기·유기·혼합자차로 분류하고, 소비자 담론과 대조한다.

왜. 우리는 `무기자차`·`유기자차`·`혼합자차`를 주제로 세고 있었지만 **실제 제품이 어떻게
구성돼 있는지는 몰랐다.** 언급 비중만 보면 "무기자차 이야기가 많다"까지만 말할 수 있고,
그것이 제품이 많아서인지 담론이 쏠려서인지 구분할 수 없다. 전성분표가 그 분모를 준다.

무엇을 비교하나. **순위만 본다.** 두 값의 분모가 다르다.
  언급 구성비  분모 = 13개 주제 전체의 언급 문서 수 (사용감 주제까지 포함)
  제품 구성비  분모 = 선크림 제품 수
크기를 나란히 놓고 비교하면 틀린다. 세 자차 유형끼리의 상대 순위는 비교할 수 있다.

분류 규칙. 제품의 전성분에 무기 차단 성분이 있으면 무기, 유기 차단 성분이 있으면 유기,
둘 다 있으면 혼합이다. 성분명은 `topics.py` 의 `mfds_inci` 를 정본으로 쓴다.

주의. 성분명에 공백이 끼어드는 파싱 오류가 있어(`에칠헥실트 리아존`) 공백을 제거하고
비교한다. 그러지 않으면 같은 성분이 여러 종으로 갈라져 제품 수가 과소 집계된다.

사용법:
    python ingredient_axis.py --ingredients "<product_ingredient_function.csv>"
"""
from __future__ import annotations

import argparse
import collections
import csv
from pathlib import Path

from topics import TOPICS

csv.field_size_limit(10 ** 8)

SUN_TERMS = [k.lower() for k in next(t for t in TOPICS if t["topic"] == "선크림")["ko"]]
FILTERS = {t["topic"]: {n.replace(" ", "") for n in t["mfds_inci"]}
           for t in TOPICS if t["topic_type"] == "formula" and t["mfds_inci"]}
FIELDS = ["filter_type", "products", "product_pct", "product_rank",
          "youtube_comment_pct", "comment_rank", "youtube_video_pct", "video_rank",
          "product_over_comment", "reading"]


def norm(value: str | None) -> str:
    """공백을 제거해 파싱 오류로 갈라진 성분명을 하나로 모은다."""
    return (value or "").replace(" ", "")


def classify(ingredients: set[str]) -> str:
    inorganic = bool(ingredients & FILTERS.get("무기자차", set()))
    organic = bool(ingredients & FILTERS.get("유기자차", set()))
    if inorganic and organic:
        return "혼합자차"
    if inorganic:
        return "무기자차"
    if organic:
        return "유기자차"
    return "차단성분 미검출"


def load_products(path: Path) -> dict[str, set[str]]:
    by_product: dict[str, set[str]] = collections.defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as h:
        for row in csv.DictReader(h):
            by_product[row["product_name"]].add(norm(row["ingredient"]))
    return by_product


def youtube_pct(judgement: Path, source: str, quarter: str) -> dict[str, float]:
    with judgement.open(encoding="utf-8-sig", newline="") as h:
        return {r["topic_id"]: float(r["composition"]) * 100
                for r in csv.DictReader(h)
                if r["source"] == source and r["quarter"] == quarter and r["composition"]}


def ranks(values: dict[str, float], keys: list[str]) -> dict[str, int]:
    ordered = sorted(keys, key=lambda k: -values.get(k, 0.0))
    return {k: i + 1 for i, k in enumerate(ordered)}


def run(ingredients: Path, judgement: Path, quarter: str, out: Path) -> None:
    by_product = load_products(ingredients)
    sun = {name: ing for name, ing in by_product.items()
           if any(t in name.lower() for t in SUN_TERMS)}
    counts = collections.Counter(classify(ing) for ing in sun.values())
    total = sum(counts.values())

    types = ["무기자차", "유기자차", "혼합자차"]
    product_pct = {t: 100 * counts[t] / total for t in types}
    cmt = youtube_pct(judgement, "youtube_comment", quarter)
    vid = youtube_pct(judgement, "youtube_video", quarter)
    r_prod, r_cmt, r_vid = ranks(product_pct, types), ranks(cmt, types), ranks(vid, types)

    rows = []
    for t in types:
        gap = r_cmt[t] - r_prod[t]
        # 분모가 다르므로 이 배수는 like-for-like 가 아니다. 세 유형끼리의 상대 크기를
        # 보는 보조 지표로만 쓰고, 판단은 순위로 한다.
        ratio = (product_pct[t] / cmt[t]) if cmt.get(t) else None
        rows.append({
            "filter_type": t,
            "products": counts[t],
            "product_pct": round(product_pct[t], 1),
            "product_rank": r_prod[t],
            "youtube_comment_pct": round(cmt.get(t, 0.0), 2),
            "comment_rank": r_cmt[t],
            "youtube_video_pct": round(vid.get(t, 0.0), 2),
            "video_rank": r_vid[t],
            "product_over_comment": None if ratio is None else round(ratio, 1),
            "reading": ("제품은 많은데 담론이 없다 — 커뮤니케이션 공백"
                        if gap >= 1 and ratio and ratio >= 5
                        else "담론이 제품 비중보다 앞선다" if gap <= -2 else ""),
        })

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"선크림 제품 {total:,}개 분류 (전성분 {len(by_product):,}개 제품 중)")
    if counts["차단성분 미검출"]:
        print(f"  차단성분 미검출 {counts['차단성분 미검출']}개는 분모에서 제외하지 않고 별도로 센다")
    print()
    print(f"{'유형':<10}{'제품':>6}{'제품%':>8}{'순위':>5}   "
          f"{'댓글%':>8}{'순위':>5}{'영상%':>8}{'순위':>5}{'배수':>7}   해석")
    for r in rows:
        ratio = "—" if r["product_over_comment"] is None else f"{r['product_over_comment']:.1f}x"
        print(f"{r['filter_type']:<10}{r['products']:>6}{r['product_pct']:>7.1f}%"
              f"{r['product_rank']:>5}   {r['youtube_comment_pct']:>7.2f}%{r['comment_rank']:>5}"
              f"{r['youtube_video_pct']:>7.2f}%{r['video_rank']:>5}{ratio:>7}   {r['reading']}")
    print()
    prod_order = [r["filter_type"] for r in sorted(rows, key=lambda r: r["product_rank"])]
    cmt_order = [r["filter_type"] for r in sorted(rows, key=lambda r: r["comment_rank"])]
    print(f"제품 순위 {' > '.join(prod_order)}")
    print(f"담론 순위 {' > '.join(cmt_order)}")
    if prod_order == list(reversed(cmt_order)):
        print("두 순위가 완전히 역전됐다. 제품이 많은 유형을 소비자가 가장 덜 말한다.")
    print("분모가 다르므로 크기가 아니라 순위를 본다. 배수는 보조 지표다.")
    print(f"{out} 저장")


def demo() -> None:
    assert norm("에칠헥실트 리아존") == "에칠헥실트리아존"
    assert classify({"징크옥사이드"}) == "무기자차"
    assert classify({"에칠헥실트리아존"}) == "유기자차"
    assert classify({"징크옥사이드", "에칠헥실트리아존"}) == "혼합자차"
    assert classify({"정제수"}) == "차단성분 미검출"
    # 정정한 표기가 사전에 실제로 들어갔는지 — 이게 없으면 전부 미검출로 떨어진다
    assert "징크옥사이드" in FILTERS["무기자차"], FILTERS["무기자차"]
    assert "부틸메톡시디벤조일메탄" in FILTERS["유기자차"]
    assert ranks({"a": 3.0, "b": 1.0}, ["a", "b"]) == {"a": 1, "b": 2}
    assert ranks({}, ["a", "b"]) == {"a": 1, "b": 2}   # 값이 없으면 입력 순서를 유지한다
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ingredients", type=Path, required=False,
                   default=Path(r"C:\Users\Admin\Documents\카카오톡 받은 파일"
                                r"\product_ingredient_function (1).csv"))
    p.add_argument("--judgement", type=Path, default=Path("reports/trend_judgement_v0.2.csv"))
    p.add_argument("--quarter", default="2026Q2")
    p.add_argument("--out", type=Path, default=Path("reports/ingredient_axis.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if not a.ingredients.exists():
        raise SystemExit(f"전성분 파일이 없다: {a.ingredients}")
    run(a.ingredients, a.judgement, a.quarter, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
