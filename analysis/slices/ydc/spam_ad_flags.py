#!/usr/bin/env python3
"""광고·협찬·홍보 문서를 표시하고, 빼도 결론이 같은지 확인한다. (기획안 §4)

기획안 §4 는 두 가지를 요구한다 — 광고·협찬을 **표시**하고, 필터에 따라 결론이
크게 달라지면 **필터 민감 신호로 표시**하라. 앞의 것만 하면 표시해 놓고 쓰지
않는 컬럼이 되므로, 뺀 계산과 넣은 계산을 나란히 돌려 차이를 낸다.
`panel_sensitivity.py` 와 같은 구조다.

표시하는 것 세 가지. 셋 다 실측으로 고른 것이고, 재보고 버린 것도 아래에 적었다.

1. `ad_video` — 광고·협찬 영상.
   `has_paid_product_placement` 는 유튜버 자체 신고라 누락이 있다
   (TEAM_DECISIONS §9). 실측으로 신고 254편 · 설명란 문구 410편이고 겹치는 것이
   196편이다. 즉 **문구만 잡히는 214편**이 신고 필드로는 안 보인다. 둘의 합집합을 쓴다.

2. `creator_comment` — 채널 운영자 본인 댓글.
   `author_channel_hash` 는 `sha256("youtube:" + channel_id)[:24]` 라 채널 id 로
   되돌려 만들 수 있다. 추정이 아니라 정확한 매칭이다. 운영자 고정 댓글은
   제품 정보·타임라인·판매 링크라서 설명란을 그대로 옮긴 것에 가깝다. 이걸 댓글
   계열에 넣으면 **소비자 반응이라는 계열의 정의가 깨진다.**

3. `promo_comment` — 판매 링크·공동구매·마켓 공지가 있는 댓글.

**버린 규칙.** 전화번호 정규식(6건)과 도박·대출 사전(4건)은 재보니 걸린 것이
거의 전부 오검출이었다 — `토토톡`, `40대출산맘`, `무향`. 0.01% 를 잡으려고
오검출을 남기지 않는다. 흔한 스팸 유형(도박·리딩방)이 이 패널에는 없다.

사용법:
    python spam_ad_flags.py data/panel/run_A data/panel/run_B ...
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
from collections import defaultdict
from pathlib import Path

import judge
import trend

csv.field_size_limit(10 ** 8)

# 신고 필드가 놓치는 협찬을 설명란 문구로 잡는다. `ppl` 은 경계를 둔다 —
# 경계 없이 부분문자열로 보면 `apple` 이 걸린다.
AD_RE = re.compile(
    r"유료\s*광고|협찬|광고\s*포함|#\s*광고|\bppl\b|제공\s*받|지원\s*받|무상\s*제공"
    r"|sponsor|paid\s+partnership|제작\s*지원", re.I)

PROMO_RE = re.compile(
    r"https?://|www\.|bit\.ly|coupa\.ng|smartstore|파트너스|판매\s*링크|구매\s*링크"
    r"|공동\s*구매|공구\s*링크|오픈\s*채팅|라이브\s*마켓|할인\s*코드|쿠폰\s*코드", re.I)

RECENT = ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]
FIELDS = ["variant", "source", "topic_id", "composition_base_pp", "composition_kept_pp",
          "diff_pp", "judged_cells", "flipped_cells"]
MATERIAL = 0.5      # panel_sensitivity.py 와 같은 기준(%p)


def creator_hash(channel_id: str) -> str:
    """youtube_collector.author_channel_hash 와 같은 식이어야 한다."""
    return hashlib.sha256(f"youtube:{channel_id}".encode("utf-8")).hexdigest()[:24]


def flag(run_dirs: list[Path], panel: dict[str, str]) -> tuple[set, set, set, dict]:
    videos = trend.load_videos(run_dirs, panel)

    # 영상 단위로 모은다. run 을 그냥 훑으면 두 run 에 있는 영상이 두 번 세어진다
    seen_video: dict[str, tuple[bool, bool]] = {}
    for run_dir in run_dirs:
        for row in trend.read_csv(run_dir / "processed" / "videos.csv"):
            vid = row["source_item_id"]
            if vid not in videos:
                continue
            seen_video[vid] = (row.get("has_paid_product_placement") == "True",
                               bool(AD_RE.search(trend.video_text(row))))
    ad = {v for v, (d, m) in seen_video.items() if d or m}
    declared = sum(1 for d, _ in seen_video.values() if d)
    matched = sum(1 for _, m in seen_video.values() if m)

    owner = {vid: creator_hash(meta["channel_id"]) for vid, meta in videos.items()}
    creator: set[tuple[str, str]] = set()
    promo: set[tuple[str, str]] = set()
    total = 0
    for run_dir in run_dirs:
        path = run_dir / "processed" / "comments.csv"
        if not path.exists():
            continue
        for row in trend.read_csv(path):
            vid = row["video_id"]
            if vid not in videos:
                continue
            text = trend.normalize_text(row.get("text"))
            if not text:
                continue
            total += 1
            key = (vid, text)
            if row.get("author_channel_hash") == owner[vid]:
                creator.add(key)
            elif PROMO_RE.search(text):
                promo.add(key)
    counts = {"videos": len(videos), "ad": len(ad), "declared": declared,
              "matched": matched, "comments": total,
              "creator": len(creator), "promo": len(promo)}
    return ad, creator, promo, counts


def measure(run_dirs: list[Path], panel: dict[str, str],
            drop_videos: set, drop_comments: set) -> dict:
    """제외 집합을 걸고 지표·판정을 다시 계산한다. 계산은 본 파이프라인 그대로다."""
    trend.EXCLUDE_VIDEOS, trend.EXCLUDE_COMMENTS = drop_videos, drop_comments
    try:
        rows = []
        for source in ("video", "comment"):
            rows.extend(trend.build_rows(run_dirs, panel, source))
    finally:
        trend.EXCLUDE_VIDEOS, trend.EXCLUDE_COMMENTS = set(), set()

    # judge 는 CSV 문자열을 읽는 함수다. 타입을 손으로 맞추면 갈라지므로 왕복시킨다.
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    judged, _ = judge.judge(list(csv.DictReader(buf)))

    mentions = defaultdict(int)
    for r in rows:
        mentions[(r["source"], r["topic_id"], r["quarter"])] += r["document_count"]
    comp = {}
    for source in ("youtube_video", "youtube_comment"):
        total = sum(mentions[(source, t, q)] for t in trend.TREND_TOPICS for q in RECENT)
        for t in trend.TREND_TOPICS:
            n = sum(mentions[(source, t, q)] for q in RECENT)
            comp[(source, t)] = 100 * n / total if total else 0.0
    types = {(r["source"], r["topic_id"], r["quarter"]): r["trend_type"]
             for r in judged if r["judged"] == "true"}
    return {"composition": comp, "types": types}


def run(run_dirs: list[Path], panel_csv: Path, out: Path) -> None:
    panel = trend.load_panel(panel_csv)
    ad, creator, promo, counts = flag(run_dirs, panel)

    print(f"선크림 장문 {counts['videos']:,}편 중 광고·협찬 {counts['ad']:,}편 "
          f"({100 * counts['ad'] / counts['videos']:.1f}%) — "
          f"신고 {counts['declared']:,} · 설명란 문구 {counts['matched']:,}")
    print(f"댓글 {counts['comments']:,}건 중 운영자 {counts['creator']:,}건 "
          f"({100 * counts['creator'] / counts['comments']:.2f}%) · "
          f"홍보 {counts['promo']:,}건 ({100 * counts['promo'] / counts['comments']:.2f}%)")
    print()

    base = measure(run_dirs, panel, set(), set())
    variants = [
        ("광고·협찬 영상 제외", ad, set()),
        ("운영자 댓글 제외", set(), creator),
        ("홍보 댓글 제외", set(), promo),
        ("전부 제외", ad, creator | promo),
    ]

    rows = []
    for name, drop_v, drop_c in variants:
        kept = measure(run_dirs, panel, drop_v, drop_c)
        # 표본이 줄어 판정이 사라진 것과 유형이 뒤집힌 것을 나눈다. 섞으면
        # "제외하니 결론이 다 바뀐다" 로 보이는데 실은 대부분 표본 미달이다.
        flips: dict[tuple[str, str], int] = defaultdict(int)
        judged_n: dict[tuple[str, str], int] = defaultdict(int)
        lost = 0
        for key, was in base["types"].items():
            judged_n[(key[0], key[1])] += 1
            now = kept["types"].get(key)
            if now is None:
                lost += 1
            elif now != was:
                flips[(key[0], key[1])] += 1
        for source in ("youtube_video", "youtube_comment"):
            for topic in trend.TREND_TOPICS:
                b = base["composition"][(source, topic)]
                k = kept["composition"][(source, topic)]
                rows.append({
                    "variant": name, "source": source, "topic_id": topic,
                    "composition_base_pp": round(b, 2),
                    "composition_kept_pp": round(k, 2),
                    "diff_pp": round(k - b, 2),
                    "judged_cells": judged_n[(source, topic)],
                    "flipped_cells": flips[(source, topic)],
                })
        mine = [r for r in rows if r["variant"] == name]
        moved = [r for r in mine if abs(r["diff_pp"]) >= MATERIAL]
        worst = max(mine, key=lambda r: abs(r["diff_pp"]))
        print(f"[{name}] 판정 {sum(judged_n.values())}셀 중 유형이 뒤집힌 것 "
              f"{sum(flips.values())}셀 · 표본 미달로 판정이 사라진 것 {lost}셀 · "
              f"구성비 {MATERIAL}%p 이상 움직인 주제 {len(moved)}개 · "
              f"최대 {worst['diff_pp']:+.2f}%p ({worst['topic_id']})")
        for r in sorted(moved, key=lambda r: -abs(r["diff_pp"]))[:5]:
            print(f"    {r['source']:<16}{r['topic_id']:<12}"
                  f"{r['composition_base_pp']:>7.2f} -> {r['composition_kept_pp']:>6.2f}"
                  f" ({r['diff_pp']:+.2f}%p)")
        for key, n in sorted(flips.items(), key=lambda kv: -kv[1]):
            if n:
                print(f"    판정 변화 {key[0]} / {key[1]} : {n}셀")

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print()
    print(f"{out} 저장")


def demo() -> None:
    # 훅이 실제로 있는지. 없으면 이 스크립트 전체가 무의미하다
    assert hasattr(trend, "EXCLUDE_VIDEOS") and hasattr(trend, "EXCLUDE_COMMENTS")
    # 해시는 수집기와 같은 식이어야 한다. 다르면 운영자 댓글이 0건으로 나온다
    import youtube_collector
    cid = "UCabc123"
    assert creator_hash(cid) == youtube_collector.author_channel_hash(
        {"authorChannelId": {"value": cid}})
    # ppl 은 경계가 있어야 한다 — 없으면 apple 이 광고로 잡힌다
    assert AD_RE.search("본 영상은 유료광고를 포함합니다")
    assert AD_RE.search("제품을 제공 받아 촬영했습니다")
    assert not AD_RE.search("애플 apple 신제품 리뷰")
    assert not AD_RE.search("supplement 후기")
    # 버린 규칙의 오검출 — 다시 넣지 않기 위해 남긴다
    assert not PROMO_RE.search("나노쿠션 토토톡 할 수 있어서 좋아요")
    assert not PROMO_RE.search("40대출산맘 입니다")
    assert PROMO_RE.search("쿠팡 파트너스 링크입니다 https://coupa.ng/x")
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dirs", nargs="*", type=Path)
    p.add_argument("--panel", type=Path, default=Path("seeds/channels_v1.csv"))
    p.add_argument("--out", type=Path, default=Path("reports/spam_ad_sensitivity.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if not a.run_dirs:
        p.error("run_dirs 를 하나 이상 지정하거나 --demo 를 쓴다")
    run(a.run_dirs, a.panel, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
