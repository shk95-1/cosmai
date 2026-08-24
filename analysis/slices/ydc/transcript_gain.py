#!/usr/bin/env python3
"""자막을 넣으면 주제 관측이 얼마나 늘어나는지 표본으로 측정한다.

왜. 기획안 §11 에 두 가지를 한계로 적어 뒀다.
  - 자막·음성은 PoC 제외이며 판정은 제목+설명+태그로만 한다
  - 쇼츠는 설명란 중앙값 0자, 주제 매칭률 24.3% 라 사실상 관측되지 않는다
둘 다 "그래서 얼마나 못 보고 있는가"를 숫자로 적지 못했다. 이 스크립트가 그 숫자를 만든다.

무엇을 비교하나. 같은 영상에 대해 두 텍스트로 `match_topics` 를 돌린다.
  기준선  document.text            = 정규화(제목 + 설명)   ← 현재 판정이 쓰는 것
  확장    document.text + 자막 본문                        ← 자막을 넣었을 때
매칭 결과의 차이가 곧 자막으로 회수되는 관측량이다.

이 측정은 판정을 바꾸지 않는다. 표본이고, 모집단 전체를 재계산하지 않는다.
자막을 본 파이프라인에 넣으면 962편 분모와 338행이 전부 움직이므로 PoC 기간에는 하지 않는다.

전제. 자막은 로컬 tubedepth 로 받는다(innertube + yt-dlp). 공식 Data API 는 자막 본문을
소유자 OAuth 없이 주지 않는다. 자료원 성격이 다르므로 이 결과는 **한계 측정용**이고
판정 근거로는 쓰지 않는다.

사용법:
    python transcript_gain.py --sample-long <ids.txt> --sample-short <ids.txt>
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from topics import TOPICS, match_topics
from trend import normalize_text

csv.field_size_limit(10 ** 8)
TREND_TOPICS = [t["topic"] for t in TOPICS if t["trend_use"]]
FIELDS = ["bucket", "topic_id", "videos_matched_baseline", "videos_matched_with_transcript",
          "gained", "gain_pct_point"]


def read_ids(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_documents(path: Path) -> dict[str, str]:
    """video_id -> 현재 판정이 쓰는 텍스트(정규화된 제목+설명)."""
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as h:
        for row in csv.DictReader(h):
            if row["source"] == "youtube_video":
                out[row["source_item_id"]] = row["text"]
    return out


PATH_RE = re.compile(r"stored\s+\d+\s+bytes at\s+(\S+)")


def collect(td: Path, video_id: str) -> Path | None:
    """tubedepth 로 자막 하나를 받고 저장 경로를 돌려준다.

    `collect` 는 블롭만 쓰고 artifacts 인덱스에는 등록하지 않는다(그건 잡 기반 수집 몫).
    그래서 DB 를 조회하지 않고 출력에 찍힌 경로를 그대로 쓴다.
    자막이 없는 영상은 0 이 아닌 코드로 끝나므로 None 이 된다.
    """
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        res = subprocess.run(
            [str(td / ".venv/Scripts/python.exe"), "-m", "tubedepth.cli",
             "collect", "video.transcript", video_id],
            cwd=td, env=env, capture_output=True, timeout=300, text=True, encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        return None
    m = PATH_RE.search((res.stdout or "") + (res.stderr or ""))
    if not m:
        return None
    blob = td / m.group(1)
    return blob if blob.exists() else None


def transcript_text(blob: Path | None) -> str | None:
    if blob is None:
        return None
    with gzip.open(blob, "rt", encoding="utf-8") as h:
        return json.load(h).get("full_text") or None


def measure(bucket: str, ids: list[str], docs: dict[str, str], td: Path) -> tuple[list[dict], dict]:
    base_hits: dict[str, set] = defaultdict(set)
    full_hits: dict[str, set] = defaultdict(set)
    stats = {"videos": 0, "transcript_ok": 0, "no_transcript": 0,
             "any_topic_baseline": 0, "any_topic_full": 0}

    for i, vid in enumerate(ids, 1):
        base = docs.get(vid)
        if base is None:
            continue
        stats["videos"] += 1
        print(f"  [{bucket} {i}/{len(ids)}] {vid}", end="\r", file=sys.stderr)
        tr = transcript_text(collect(td, vid))
        if tr:
            stats["transcript_ok"] += 1
        else:
            stats["no_transcript"] += 1

        b = match_topics(base)
        f = match_topics(normalize_text(base + " " + tr) if tr else base)
        for t in b:
            base_hits[t].add(vid)
        for t in f:
            full_hits[t].add(vid)
        if b:
            stats["any_topic_baseline"] += 1
        if f:
            stats["any_topic_full"] += 1

    rows = []
    n = max(1, stats["videos"])
    for topic in TREND_TOPICS:
        b, f = len(base_hits[topic]), len(full_hits[topic])
        rows.append({
            "bucket": bucket,
            "topic_id": topic,
            "videos_matched_baseline": b,
            "videos_matched_with_transcript": f,
            "gained": f - b,
            "gain_pct_point": round(100 * (f - b) / n, 1),
        })
    rows.sort(key=lambda r: -r["gained"])
    return rows, stats


def demo() -> None:
    """자막을 붙였을 때 없던 주제가 잡히는지만 확인한다."""
    base = normalize_text("여름 선크림 추천")
    assert "백탁" not in match_topics(base)
    withtr = normalize_text(base + " 백탁 하나도 없고 눈시림도 없어요")
    got = match_topics(withtr)
    assert "백탁" in got and "자극_눈시림" in got, got
    assert len(match_topics(withtr)) > len(match_topics(base))
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tubedepth", type=Path,
                   default=Path(r"C:\Users\Admin\Downloads\yt-scrapper-dev\yt-scrapper-dev"))
    p.add_argument("--documents", type=Path, default=Path("common/document.csv"))
    p.add_argument("--sample-long", type=Path)
    p.add_argument("--sample-short", type=Path)
    p.add_argument("--out", type=Path, default=Path("reports/transcript_gain.csv"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    if not (a.sample_long or a.sample_short):
        p.error("--sample-long 또는 --sample-short 중 하나는 필요하다")

    docs = load_documents(a.documents)
    all_rows, summary = [], {}
    for bucket, path in (("장문", a.sample_long), ("쇼츠", a.sample_short)):
        if not path:
            continue
        rows, stats = measure(bucket, read_ids(path), docs, a.tubedepth)
        all_rows += rows
        summary[bucket] = stats

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)

    print(" " * 60, file=sys.stderr)
    for bucket, s in summary.items():
        n = max(1, s["videos"])
        print(f"[{bucket}] 표본 {s['videos']}편 · 자막 확보 {s['transcript_ok']}"
              f" · 없음 {s['no_transcript']}")
        print(f"   주제가 하나라도 잡힌 영상 : {s['any_topic_baseline']} "
              f"({100*s['any_topic_baseline']/n:.1f}%) "
              f"-> {s['any_topic_full']} ({100*s['any_topic_full']/n:.1f}%)")
        gains = [r for r in all_rows if r["bucket"] == bucket and r["gained"] > 0]
        print(f"   주제별 회수 상위:")
        for r in gains[:6]:
            print(f"     {r['topic_id']:<18} {r['videos_matched_baseline']:>3}"
                  f" -> {r['videos_matched_with_transcript']:>3}"
                  f"  (+{r['gained']}, +{r['gain_pct_point']}%p)")
        print()
    print(f"{a.out} 저장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
