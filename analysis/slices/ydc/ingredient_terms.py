#!/usr/bin/env python3
"""유튜브에서 실제로 쓰이는 성분·제형 표기 빈도를 센다. 별칭 사전(③ 담당) 인계용.

사전의 표기(ko·latin)와 식약처 성분명(mfds_inci)이 각각 유튜브 코퍼스에서
몇 문서에 등장하는지 센다. 목적은 두 가지다.

  1. 어떤 표기가 실제로 쓰이는지 → 별칭 사전에 넣을 표기를 실측으로 고른다.
  2. 식약처 성분명이 유튜브에 등장하는지 → 등장이 0이면 `mfds_inci` 매핑 열이
     없는 한 두 소스가 연결되지 않는다는 뜻이다.

문서 단위로 센다. 같은 문서에 같은 표기가 여러 번 나와도 1로 센다
(기획안 §7: 반복 횟수보다 등장 문서 수를 우선한다).

사용법:
    python ingredient_terms.py common/document.csv --out reports/youtube_ingredient_terms.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from topics import TOPICS, _latin_pattern

csv.field_size_limit(10 ** 8)

FIELDS = ["topic_id", "topic_type", "term", "term_kind",
          "video_docs", "comment_docs", "total_docs"]


def build_terms() -> list[tuple[str, str, str, str]]:
    """(topic_id, topic_type, term, term_kind). mfds_inci 는 latin 여부와 무관하게 경계 매칭한다."""
    out = []
    for t in TOPICS:
        for term in t["ko"]:
            out.append((t["topic"], t["topic_type"], term, "ko"))
        for term in t["latin"]:
            out.append((t["topic"], t["topic_type"], term, "latin"))
        for term in t["mfds_inci"]:
            kind = "mfds_inci"
            out.append((t["topic"], t["topic_type"], term, kind))
    return out


def count(document_csv: Path) -> dict[tuple[str, str, str], dict[str, int]]:
    terms = build_terms()
    # 영문·로마자 표기는 경계 매칭한다. 부분문자열로 하면 PA 가 coupang 에 걸려
    # 오탐이 16% 났다(실측). 한글 표기는 조사가 붙으므로 부분문자열이 맞다.
    patterns = {}
    for topic, ttype, term, kind in terms:
        if term.isascii():
            patterns[(topic, term, kind)] = _latin_pattern([term])
        else:
            patterns[(topic, term, kind)] = None

    tally: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"video_docs": 0, "comment_docs": 0})
    scanned = {"video": 0, "comment": 0}

    with document_csv.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            text = row.get("text") or ""
            if not text:
                continue
            bucket = "comment_docs" if row["source"] == "youtube_comment" else "video_docs"
            scanned["comment" if bucket == "comment_docs" else "video"] += 1
            lowered = text.lower()
            for topic, ttype, term, kind in terms:
                pattern = patterns[(topic, term, kind)]
                hit = pattern.search(text) if pattern else (term.lower() in lowered)
                if hit:
                    tally[(topic, term, kind)][bucket] += 1
    tally["__scanned__"] = scanned  # type: ignore[index]
    return tally


def demo() -> None:
    assert ("무기자차", "formula", "산화아연", "mfds_inci") in build_terms()
    assert ("무기자차", "formula", "징크", "ko") in build_terms()
    pat = _latin_pattern(["PA"])
    assert pat is not None
    assert pat.search("SPF50+ PA++++")
    assert not pat.search("coupang"), "경계 매칭이어야 coupang 오탐이 안 난다"
    print("demo ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("document_csv", nargs="?", type=Path,
                        default=Path("common/document.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("reports/youtube_ingredient_terms.csv"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()

    if args.demo:
        demo()
        return 0

    tally = count(args.document_csv)
    scanned = tally.pop("__scanned__")  # type: ignore[arg-type]
    meta = {(t[0], t[2], t[3]): t[1] for t in build_terms()}

    rows = []
    for (topic, term, kind), c in tally.items():
        rows.append({
            "topic_id": topic,
            "topic_type": meta[(topic, term, kind)],
            "term": term,
            "term_kind": kind,
            "video_docs": c["video_docs"],
            "comment_docs": c["comment_docs"],
            "total_docs": c["video_docs"] + c["comment_docs"],
        })
    for topic, ttype, term, kind in build_terms():          # 0건도 남긴다 — 없다는 사실이 근거다
        if (topic, term, kind) not in tally:
            rows.append({"topic_id": topic, "topic_type": ttype, "term": term,
                         "term_kind": kind, "video_docs": 0, "comment_docs": 0,
                         "total_docs": 0})
    rows.sort(key=lambda r: (r["topic_id"], -r["total_docs"], r["term"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    mfds = [r for r in rows if r["term_kind"] == "mfds_inci"]
    print(f"스캔: 영상 {scanned['video']:,} / 댓글 {scanned['comment']:,}")
    print(f"{args.out} : {len(rows)}행")
    print()
    print("식약처 성분명의 유튜브 등장 문서 수:")
    for r in sorted(mfds, key=lambda r: -r["total_docs"]):
        print(f"  {r['term']:<28} 영상 {r['video_docs']:>6,}  댓글 {r['comment_docs']:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
