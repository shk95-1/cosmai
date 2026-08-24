#!/usr/bin/env python3
"""선크림 주제별 분기 시계열. 수집 run 여러 개를 합쳐 하나의 패널로 본다.

정의는 reports/TEAM_DECISIONS_v0.2.md를 따른다. 왜 이 정의인지 요약:

1. 지표는 문서 기준 share가 아니라 **주제 간 구성비**다.
   유튜버 설명란 길이 중앙값이 3년간 1,253자 -> 709자로 줄었다. 문서 기준
   share는 분자(언급 수)만 줄고 분모(영상 수)는 그대로여서 13개 주제 중
   10개가 동반 하락한다(합계 -28.6%p). 구성비는 분자·분모가 같이 줄어 상쇄된다.

2. 분모는 **장문(>60초)만**이다.
   쇼츠는 설명란 길이 중앙값이 0자고 매칭률이 24%(장문 64%)다. 게다가 쇼츠
   비중이 분기마다 55%~41%로 움직여서, 한 분모에 넣으면 포맷 선택 변화가
   주제 트렌드로 위장된다.

3. 비교는 분기 대 분기가 아니라 **전년 동분기(YoY)**다.
   선크림은 계절 상품이다. 장문 중 선크림 언급 비중이 3년 연속 Q2 최고
   (26.7/25.3/25.6%), Q1·Q4 최저(17.7/14.6%, 19.2/15.4%)다. 인접 분기를
   비교하면 매년 Q2에 급상승, Q4에 사라짐이 나온다.

4. 영상 설명과 댓글은 **합치지 않고 나란히** 낸다.
   둘이 다른 것을 측정한다. 영상 설명은 스펙·포뮬러(SPF는 영상 11/13분기 vs
   댓글 9/13), 댓글은 사용감·불만(백탁은 영상 0/13분기 vs 댓글 12/13).
   가중합으로 섞으면 "소비자는 불만을 말하는데 제작자는 안 다루는 주제"라는
   제품 공백 신호가 사라진다.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import re
import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from topics import TOPICS, match_topics

csv.field_size_limit(10 ** 7)

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE_RE = re.compile(r"\s+")

METRIC_VERSION = "v0.2"
SHORTS_MAX_SECONDS = 60
MIN_DOCUMENT_COUNT = 5  # 이 미만이면 표본 부족으로 velocity를 내지 않는다
TREND_TOPICS = [t["topic"] for t in TOPICS if t["trend_use"]]

# 포함·제외 비교용 훅. 기본은 비어 있어서 아무것도 바뀌지 않는다.
# `spam_ad_flags.py` 가 여기에 광고 영상 id 와 (video_id, 정규화 텍스트) 를 채워
# 같은 계산을 두 번 돌린다. 계산을 두 벌 만들면 결과가 갈라지므로 훅만 둔다.
EXCLUDE_VIDEOS: set[str] = set()
EXCLUDE_COMMENTS: set[tuple[str, str]] = set()

# 후향 검증용 훅. 이 분기 이후를 없는 것처럼 계산한다. `backtest.py` 가 채운다.
CUTOFF_QUARTER: str | None = None
SUNSCREEN_TERMS = [k.lower() for k in next(t for t in TOPICS if t["topic"] == "선크림")["ko"]]


def quarter(month: str) -> str:
    """'2026-07' -> '2026Q3'"""
    year, mon = month.split("-")
    return f"{year}Q{(int(mon) - 1) // 3 + 1}"


def previous_year_quarter(q: str) -> str:
    """'2026Q3' -> '2025Q3'"""
    return f"{int(q[:4]) - 1}Q{q[5]}"


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def load_panel(path: Path) -> dict[str, str]:
    """channel_id -> panel_role. 패널 정의는 이 파일이 정본이다."""
    return {r["channel_id"]: r.get("panel_role") or "unset" for r in read_csv(path)}


def is_sunscreen(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in SUNSCREEN_TERMS)


def normalize_text(value: str | None) -> str:
    """HTML 엔티티 해제 → 유니코드 NFKC → 제어문자 제거 → 공백 축약.

    실측(2026-08-19)으로 이 정규화가 주제 매칭 결과를 바꾸는 경우는 영상 5,957편 중
    3편(0.05%), 댓글 210,825건 중 0건이었다. 수집기가 `textFormat=plainText`로 받아
    HTML이 애초에 들어오지 않고, 사전이 부분문자열 매칭이라 공백 축약에 둔감하다.

    그래도 적용하는 이유는 두 가지다.
    1. 중복 댓글 판정의 기준이 필요하다. 정규화 없이 비교하면 공백만 다른 복붙이
       서로 다른 댓글로 남는다(실측 2,395건 = 1.1%).
    2. NAVER 블로그·뉴스처럼 HTML이 그대로 오는 소스가 붙으면 같은 계약이 필요하다.
       소스마다 정규화 규칙이 다르면 소스 간 비교가 무의미해진다.
    """
    text = html.unescape(value or "")
    text = unicodedata.normalize("NFKC", text)
    return WHITESPACE_RE.sub(" ", CONTROL_RE.sub("", text)).strip()


def video_text(row: dict[str, str]) -> str:
    return normalize_text(f"{row.get('title') or ''} {row.get('description') or ''}")


def load_videos(run_dirs: Iterable[Path], panel: dict[str, str]) -> dict[str, dict[str, Any]]:
    """선크림·장문·product 영상만 남긴다. video_id -> {quarter, channel_id, topics}

    같은 영상이 여러 run에 있으면 뒤에 온 것으로 덮는다. run 사이에 채널이
    겹치지 않게 수집했으므로 실제로는 발생하지 않지만, 중복 수집해도
    분모가 두 번 세어지지 않도록 video_id를 키로 둔다.
    """
    videos: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        for row in read_csv(run_dir / "processed" / "videos.csv"):
            if panel.get(row["channel_id"]) != "product":
                continue
            if row["source_item_id"] in EXCLUDE_VIDEOS:
                continue
            duration = row.get("duration_seconds")
            if not duration or int(duration) <= SHORTS_MAX_SECONDS:
                continue  # 쇼츠와 길이 없는 영상(라이브 등)은 제외
            month = row.get("analysis_month")
            if not month:
                continue
            text = video_text(row)
            if not is_sunscreen(text):
                continue
            videos[row["source_item_id"]] = {
                "quarter": quarter(month),
                "channel_id": row["channel_id"],
                "topics": match_topics(text),
            }
    return videos


def count_mentions(
    run_dirs: Iterable[Path], videos: dict[str, dict[str, Any]], source: str
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], set[str]], dict[str, int]]:
    """(주제, 분기) -> 언급 수 / 채널 집합, 그리고 분기 -> 문서 수.

    source="video"는 영상 제목+설명, source="comment"는 부모 영상에 귀속된 댓글.
    댓글의 published_at은 댓글 자체 시각이라 쓰지 않는다. 3년 전 영상에 어제
    댓글이 달리므로 댓글 시각으로 분기를 만들면 분모가 정의되지 않는다.
    """
    mentions: dict[tuple[str, str], int] = defaultdict(int)
    channels: dict[tuple[str, str], set[str]] = defaultdict(set)
    documents: dict[str, int] = defaultdict(int)
    # 중복 제거 전 언급 수. evidence_strength의 '비중복 비율' 항이 이 값을 쓴다.
    raw: dict[tuple[str, str], int] = defaultdict(int)

    if source == "video":
        for meta in videos.values():
            documents[meta["quarter"]] += 1
            for topic in meta["topics"]:
                mentions[(topic, meta["quarter"])] += 1
                raw[(topic, meta["quarter"])] += 1
                channels[(topic, meta["quarter"])].add(meta["channel_id"])
        return mentions, channels, documents, raw

    # 같은 영상 안에서 정규화 후 동일한 댓글은 한 번만 센다. 복붙 스팸과
    # `❤`·`감사합니다` 류가 언급량을 부풀리는 것을 막는다(실측 2,395건 = 1.1%).
    # 영상 간 중복은 제거하지 않는다 — 다른 영상에 달린 같은 말은 각각 실제 반응이다.
    seen: set[tuple[str, str]] = set()
    # 같은 댓글이 두 run 에 있으면 한 번만 센다. run 을 겹치지 않게 수집했으므로
    # 지금은 걸리는 것이 없지만, 실수로 같은 run 을 두 번 넘기면 `raw` 만 두 배가
    # 되어 unique_ratio 가 절반으로 떨어진다(reproduce.py 3번 검사가 이걸 잡았다).
    seen_ids: set[str] = set()
    for run_dir in run_dirs:
        path = run_dir / "processed" / "comments.csv"
        if not path.exists():
            continue
        for row in read_csv(path):
            meta = videos.get(row["video_id"])
            if not meta:
                continue
            if row["comment_id"] in seen_ids:
                continue
            seen_ids.add(row["comment_id"])
            text = normalize_text(row.get("text"))
            if not text:
                continue
            key = (row["video_id"], text)
            if key in EXCLUDE_COMMENTS:
                continue      # raw 보다 앞에서 뺀다. 뒤에서 빼면 unique_ratio 만 낮아진다
            topics = match_topics(text)
            for topic in topics:                      # 중복 포함 — 비중복 비율의 분모
                raw[(topic, meta["quarter"])] += 1
            if key in seen:
                continue
            seen.add(key)
            documents[meta["quarter"]] += 1
            for topic in topics:
                mentions[(topic, meta["quarter"])] += 1
                channels[(topic, meta["quarter"])].add(meta["channel_id"])
    return mentions, channels, documents, raw


def entropy(counts: list[int]) -> float:
    """정규화 섀넌 엔트로피. 채널 1개면 0, 고르게 퍼지면 1."""
    total = sum(counts)
    if total == 0 or len(counts) <= 1:
        return 0.0
    probs = [c / total for c in counts if c]
    raw = -sum(p * math.log(p) for p in probs)
    return raw / math.log(len(counts))


def build_rows(run_dirs: list[Path], panel: dict[str, str], source: str) -> list[dict[str, Any]]:
    videos = load_videos(run_dirs, panel)
    mentions, channels, documents, raw = count_mentions(run_dirs, videos, source)
    quarters = sorted(documents)
    if CUTOFF_QUARTER:
        # 후향 검증용. 이 분기까지만 알고 있었던 것처럼 계산한다. persistence 의
        # baseline 이 전체 기간 중앙값이라 자르지 않으면 미래를 보고 판정한다.
        quarters = [q for q in quarters if q <= CUTOFF_QUARTER]
    panel_channels = {q: set() for q in quarters}
    for meta in videos.values():
        if meta["quarter"] in panel_channels:
            panel_channels[meta["quarter"]].add(meta["channel_id"])

    # 주제별 채널 분포는 엔트로피용. 영상 단위로만 세면 되므로 source와 무관하다.
    per_channel: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for meta in videos.values():
        for topic in meta["topics"]:
            per_channel[(topic, meta["quarter"])][meta["channel_id"]] += 1

    totals = {q: sum(mentions[(t, q)] for t in TREND_TOPICS) for q in quarters}
    composition = {
        (t, q): (mentions[(t, q)] / totals[q] if totals[q] else 0.0)
        for t in TREND_TOPICS
        for q in quarters
    }
    baseline = {
        t: statistics.median([composition[(t, q)] for q in quarters]) for t in TREND_TOPICS
    }

    rows: list[dict[str, Any]] = []
    for topic in TREND_TOPICS:
        for index, q in enumerate(quarters):
            doc_count = mentions[(topic, q)]
            prev = previous_year_quarter(q)
            velocity = None
            if prev in totals and doc_count >= MIN_DOCUMENT_COUNT and mentions[(topic, prev)] >= MIN_DOCUMENT_COUNT:
                velocity = math.log(composition[(topic, q)]) - math.log(composition[(topic, prev)])
            window = quarters[max(0, index - 3): index + 1]
            dist = per_channel[(topic, q)]
            rows.append(
                {
                    "quarter": q,
                    "category": "선크림",
                    "topic_id": topic,
                    "source": f"youtube_{source}",
                    "content_type": "long_form",
                    "document_count": doc_count,
                    "quarter_documents": documents[q],
                    "quarter_mentions": totals[q],
                    "composition": round(composition[(topic, q)], 5),
                    "velocity_yoy": round(velocity, 4) if velocity is not None else None,
                    "persistence": round(
                        sum(1 for w in window if composition[(topic, w)] > baseline[topic]) / len(window), 3
                    ),
                    # 판정 규칙(TEAM_DECISIONS §3.2)은 개수 단위로 쓰여 있다. 비율만 두면
                    # 창이 짧은 초기 분기에서 개수를 복원할 수 없으므로 둘 다 남긴다.
                    "persistence_count": sum(
                        1 for w in window if composition[(topic, w)] > baseline[topic]
                    ),
                    "window_quarters": len(window),
                    "unique_ratio": round(
                        doc_count / raw[(topic, q)] if raw[(topic, q)] else 1.0, 4
                    ),
                    "channel_count": len(channels[(topic, q)]),
                    "panel_channels": len(panel_channels[q]),
                    "channel_diffusion": round(
                        0.5 * (len(dist) / len(panel_channels[q]) if panel_channels[q] else 0)
                        + 0.5 * entropy(list(dist.values())),
                        3,
                    ),
                    "sample_ok": doc_count >= MIN_DOCUMENT_COUNT,
                    "metric_version": METRIC_VERSION,
                }
            )
    return rows


def demo() -> None:
    # 정규화: 엔티티 해제, NFKC, 제어문자 제거, 공백 축약
    assert normalize_text("&#39;백탁&#39;") == "'백탁'"
    assert normalize_text("백탁   없이\n\n촉촉") == "백탁 없이 촉촉"
    assert normalize_text("ＳＰＦ５０") == "SPF50"  # 전각 -> 반각
    assert normalize_text(None) == ""
    assert normalize_text("  ") == ""
    assert quarter("2026-01") == "2026Q1"
    assert quarter("2026-07") == "2026Q3"
    assert quarter("2026-12") == "2026Q4"
    assert previous_year_quarter("2026Q3") == "2025Q3"
    assert previous_year_quarter("2024Q1") == "2023Q1"
    assert is_sunscreen("올리브영 선크림 추천")
    assert is_sunscreen("자외선차단제 비교")
    assert not is_sunscreen("겨울 보습크림 리뷰")
    # 엔트로피: 한 채널 독점은 0, 고르게 퍼지면 1
    assert entropy([10]) == 0.0
    assert entropy([]) == 0.0
    assert abs(entropy([5, 5]) - 1.0) < 1e-9
    assert entropy([9, 1]) < entropy([5, 5])
    # 구성비는 분모가 같이 줄면 불변이다 — 이게 설명란 드리프트를 상쇄하는 근거
    assert abs((10 / 100) - (5 / 50)) < 1e-9
    print("[demo] 통과")


def main() -> int:
    parser = argparse.ArgumentParser(description="선크림 주제별 분기 시계열 (v0.2 정의)")
    parser.add_argument("run_dir", type=Path, nargs="*", help="수집 run 디렉터리 (여러 개 가능)")
    parser.add_argument("--panel", type=Path, default=Path("seeds/channels_v1.csv"))
    parser.add_argument("--source", choices=["video", "comment", "both"], default="both")
    parser.add_argument("--out", type=Path, help="CSV 저장 경로. 없으면 표준출력")
    parser.add_argument("--demo", action="store_true", help="자체 점검만 실행")
    args = parser.parse_args()

    if args.demo:
        demo()
        return 0
    if not args.run_dir:
        parser.error("run_dir을 하나 이상 지정하세요.")

    panel = load_panel(args.panel)
    sources = ["video", "comment"] if args.source == "both" else [args.source]
    rows: list[dict[str, Any]] = []
    for source in sources:
        rows.extend(build_rows(list(args.run_dir), panel, source))

    handle = args.out.open("w", encoding="utf-8-sig", newline="") if args.out else sys.stdout
    try:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.out:
            handle.close()
            print(f"[out] {args.out} — {len(rows)}행", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
