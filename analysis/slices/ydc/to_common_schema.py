#!/usr/bin/env python3
"""유튜브 수집 결과를 팀 공통 스키마(document / mention / channel)로 변환한다.

기획안 §6 데이터 흐름과 TEAM_DECISIONS_v0.2 §1의 합의를 그대로 따른다.

  document  문서 1건 = 1행. 영상과 댓글이 같은 표에 들어간다.
            parent_item_id·channel_id·content_type 이 있어야 댓글이 어느 영상에
            달렸는지 알 수 있다. 애매한 API 필드는 source_metadata JSON 으로 받는다.
  mention   (문서, 주제) 1건 = 1행. 15개 주제 전부 내보내고 trend_use 로 구분한다.
            판정용 13개만 쓰려면 trend_use=true 로 필터한다.
  channel   panel_role 조인용. product / expert 필터가 모든 지표의 분모를 정한다.

분기(quarter)는 저장하지 않는다. 파생값이고, 댓글은 자기 시각이 아니라
parent_item_id 로 부모 영상에 조인해 계산해야 한다. 규칙은 manifest 에 적는다.

사용법:
    python to_common_schema.py data/panel/run_A data/panel/run_B --out common
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from topics import TOPICS, match_topics
from trend import normalize_text  # 정규화 규칙은 한 군데만 둔다. 두 벌이면 소스 간 비교가 무의미해진다

SHORTS_MAX_SECONDS = 60
TOPIC_META = {t["topic"]: t for t in TOPICS}

DOCUMENT_FIELDS = [
    "doc_id", "source", "source_item_id", "content_type",
    "parent_item_id", "channel_id", "published_at", "url", "text",
    "quality_flags", "source_metadata",
]
MENTION_FIELDS = ["doc_id", "topic_id", "topic_type", "trend_use", "matched_term", "span_start"]
CHANNEL_FIELDS = ["channel_id", "channel_title", "panel_role", "team_role", "uploads_playlist_id"]


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def video_text(row: dict[str, str]) -> str:
    """제목 + 설명. `trend.py`와 같은 규칙이어야 한다.

    태그는 넣지 않고 source_metadata 로 보낸다. 기획안 문장은 "제목+설명+태그"인데
    보고한 숫자(선크림 장문 962편)는 태그 없이 나온 값이다. 태그를 넣으면 1,019편이
    되고 모든 composition 이 움직인다. 판정 기준을 바꾸는 결정은 팀 합의 사항이므로,
    지금은 보고한 숫자를 재현하는 쪽으로 두고 태그는 메타데이터에 남겨 둔다.
    재계산이 필요하면 재수집 없이 metadata 의 tags 로 다시 만들 수 있다.
    """
    return normalize_text(f"{row.get('title') or ''} {row.get('description') or ''}")


def video_tags(row: dict[str, str]) -> list[str]:
    try:
        tags = json.loads(row.get("tags_json") or "[]")
    except json.JSONDecodeError:
        return []
    return tags if isinstance(tags, list) else []


def content_type_of(row: dict[str, str]) -> str:
    raw = (row.get("duration_seconds") or "").strip()
    if not raw:
        return "video_unknown"          # 라이브 등. 쇼츠·장문 양쪽에서 제외한다
    try:
        seconds = float(raw)
    except ValueError:
        return "video_unknown"
    return "video_short" if seconds <= SHORTS_MAX_SECONDS else "video_long"


def first_span(text: str, topic: str) -> tuple[str, int]:
    """매칭된 용어와 첫 등장 위치.

    ponytail: 첫 등장 하나만 잡는다. 근거 카드에서 문장을 보여주는 데는 충분하고,
    전체 위치가 필요해지면 같은 루프에서 finditer 로 바꾸면 된다.
    """
    entry = TOPIC_META[topic]
    lowered = text.lower()
    best: tuple[str, int] | None = None
    for term in entry["ko"]:
        at = lowered.find(term.lower())
        if at >= 0 and (best is None or at < best[1]):
            best = (term, at)
    for term in entry["latin"]:
        at = lowered.find(term.lower())
        if at >= 0 and (best is None or at < best[1]):
            best = (term, at)
    return best if best else ("", -1)


def mentions_for(doc_id: str, text: str) -> list[dict[str, Any]]:
    rows = []
    for topic in match_topics(text, include_excluded=True):
        term, at = first_span(text, topic)
        rows.append({
            "doc_id": doc_id,
            "topic_id": topic,
            "topic_type": TOPIC_META[topic]["topic_type"],
            "trend_use": str(TOPIC_META[topic]["trend_use"]).lower(),
            "matched_term": term,
            "span_start": at,
        })
    return rows


def convert(run_dirs: list[Path], out_dir: Path) -> dict[str, Any]:
    documents: dict[str, dict[str, Any]] = {}
    mentions: list[dict[str, Any]] = []
    channels: dict[str, dict[str, Any]] = {}
    video_channel: dict[str, str] = {}
    seen_comment_text: set[tuple[str, str]] = set()
    counts = {"video_rows": 0, "comment_rows": 0, "duplicate_docs": 0,
              "orphan_comments": 0, "duplicate_comment_text": 0}
    source_runs = []

    for run_dir in run_dirs:
        processed = run_dir / "processed"
        manifest_path = run_dir / "manifest.json"
        source_runs.append(json.loads(manifest_path.read_text(encoding="utf-8"))
                           if manifest_path.exists() else {"run_id": run_dir.name})

        for row in read_csv(processed / "channels.csv"):
            channels[row["channel_id"]] = {
                "channel_id": row["channel_id"],
                "channel_title": row.get("channel_title"),
                "panel_role": row.get("panel_role"),
                "team_role": row.get("team_role"),
                "uploads_playlist_id": row.get("uploads_playlist_id"),
            }

        for row in read_csv(processed / "videos.csv"):
            counts["video_rows"] += 1
            video_id = row["source_item_id"]
            doc_id = "youtube_video:" + video_id
            if doc_id in documents:
                counts["duplicate_docs"] += 1
                continue
            video_channel[video_id] = row.get("channel_id") or ""
            text = video_text(row)
            documents[doc_id] = {
                "doc_id": doc_id,
                "source": "youtube_video",
                "source_item_id": video_id,
                "content_type": content_type_of(row),
                "parent_item_id": "",
                "channel_id": row.get("channel_id"),
                "published_at": row.get("published_at"),
                "url": row.get("canonical_url"),
                "text": text,
                "quality_flags": "" if text else "empty_text",
                "source_metadata": json.dumps({
                    "tags": video_tags(row),
                    "duration_seconds": row.get("duration_seconds") or None,
                    "view_count": row.get("view_count") or None,
                    "like_count": row.get("like_count") or None,
                    "comment_count": row.get("comment_count") or None,
                    "has_paid_product_placement": row.get("has_paid_product_placement") or None,
                    "caption_available": row.get("caption_available") or None,
                    "category_id": row.get("category_id") or None,
                    "collected_at": row.get("collected_at") or None,
                }, ensure_ascii=False),
            }
            mentions.extend(mentions_for(doc_id, text))

        for row in read_csv(processed / "comments.csv"):
            counts["comment_rows"] += 1
            comment_id = row["comment_id"]
            doc_id = "youtube_comment:" + comment_id
            if doc_id in documents:
                counts["duplicate_docs"] += 1
                continue
            video_id = row.get("video_id") or ""
            if video_id not in video_channel:
                counts["orphan_comments"] += 1
            text = normalize_text(row.get("text"))
            # 같은 영상 안에서 정규화 후 동일한 댓글은 중복으로 표시한다. 복붙 스팸과
            # `❤`·`감사합니다` 류가 언급량을 부풀린다(실측 1.1%). 행은 지우지 않는다 —
            # 기획안 §4가 삭제 대신 플래그 보존과 포함·제외 비교를 요구한다.
            # 영상 간 중복은 표시하지 않는다. 다른 영상에 달린 같은 말은 각각 실제 반응이다.
            flags = []
            if not text:
                flags.append("empty_text")
            elif (video_id, text) in seen_comment_text:
                flags.append("duplicate_in_parent")
                counts["duplicate_comment_text"] += 1
            else:
                seen_comment_text.add((video_id, text))
            documents[doc_id] = {
                "doc_id": doc_id,
                "source": "youtube_comment",
                "source_item_id": comment_id,
                "content_type": "comment",
                "parent_item_id": video_id,
                "channel_id": video_channel.get(video_id, ""),
                "published_at": row.get("published_at"),
                "url": row.get("canonical_video_url"),
                "text": text,
                "quality_flags": ",".join(flags),
                "source_metadata": json.dumps({
                    "like_count": row.get("like_count") or None,
                    "is_reply": row.get("is_reply") or None,
                    "thread_id": row.get("thread_id") or None,
                    "parent_comment_id": row.get("parent_comment_id") or None,
                    "total_reply_count": row.get("total_reply_count") or None,
                    "author_channel_hash": row.get("author_channel_hash") or None,
                    "collected_at": row.get("collected_at") or None,
                }, ensure_ascii=False),
            }
            mentions.extend(mentions_for(doc_id, text))

    doc_rows = list(documents.values())
    written = {
        "document.csv": write_csv(out_dir / "document.csv", DOCUMENT_FIELDS, doc_rows),
        "mention.csv": write_csv(out_dir / "mention.csv", MENTION_FIELDS, mentions),
        "channel.csv": write_csv(out_dir / "channel.csv", CHANNEL_FIELDS, list(channels.values())),
    }

    by_type: dict[str, int] = {}
    for row in doc_rows:
        by_type[row["content_type"]] = by_type.get(row["content_type"], 0) + 1

    manifest = {
        "produced_by": "to_common_schema.py",
        "source_runs": [r.get("run_id") for r in source_runs],
        "table_counts": written,
        "documents_by_content_type": by_type,
        "input_counts": counts,
        "text_rule": (
            "영상 text = 정규화(제목 + 공백 + 설명). 댓글 text = 정규화(본문). "
            "정규화는 HTML 엔티티 해제 → NFKC → 제어문자 제거 → 공백 축약이며 trend.py 의 normalize_text 를 그대로 쓴다. "
            "태그는 text 에 넣지 않고 source_metadata.tags 로 보낸다. 자막·음성은 PoC 제외."
        ),
        "rules": [
            "유일키는 source + source_item_id 다. doc_id 는 그 둘을 콜론으로 이은 값이다.",
            "분기는 저장하지 않는다. published_at 의 연·월로 달력 분기를 만든다(수집 13,979편 전부 analysis_month 와 일치함을 확인).",
            "댓글은 published_at 이 자기 시각이므로 분기 판정에 쓰지 않는다. parent_item_id 로 부모 영상에 조인해 부모의 분기에 배정한다.",
            "트렌드 판정 분모는 content_type = video_long 만 쓴다. video_short 는 별도 계열, video_unknown 은 양쪽에서 제외한다.",
            "판정·보고 모집단은 channel.panel_role = product 로 한정한다.",
            "선크림 모집단 필터는 topic_id = 선크림(trend_use = false)으로 만든다.",
            "mention 은 주제 15개 전부를 담는다. 판정용 13개는 trend_use = true 로 필터한다.",
            "행을 지우지 않는다. 품질 문제는 quality_flags 로 표시한다(empty_text, duplicate_in_parent).",
            "언급량 집계에서는 quality_flags 가 빈 문서만 센다. duplicate_in_parent 는 같은 영상 안 복붙이라 반응 1건으로 보지 않는다.",
            "댓글은 주제 사전에 걸린 영상만 수집했다. 전체 영상에 대한 댓글 분모는 존재하지 않는다.",
            "태그를 판정 텍스트에 포함할지는 미결이다. 포함하면 선크림 장문이 962 → 1,019편이 되고 모든 composition 이 움직인다.",
        ],
        "reproduces": {
            "선크림_장문_product": 964,
            "그_영상_댓글_전체": 60348,
            "그_영상_댓글_중복제외": 60311,
            "재현_방법": (
                "document 에서 content_type=video_long 이고 channel.panel_role=product 이며 "
                "mention 에 topic_id=선크림 이 있는 문서 → 964. "
                "그 영상들을 parent_item_id 로 갖는 댓글 → 60,348. "
                "그중 quality_flags 가 빈 것 → 60,311."
            ),
            "주의": (
                "제출본 기획안에는 962편으로 적혀 있으나 trend.py 를 지금 돌리면 964편이다. "
                "962 는 옛 값이므로 성능·한계 보고서에는 964 를 쓴다."
            ),
        },
        "source_run_manifests": source_runs,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def demo() -> None:
    """스키마 계약 자체를 검사한다. 이게 깨지면 통합이 조용히 어긋난다."""
    assert content_type_of({"duration_seconds": "60"}) == "video_short"
    assert content_type_of({"duration_seconds": "61"}) == "video_long"
    assert content_type_of({"duration_seconds": ""}) == "video_unknown"
    assert content_type_of({"duration_seconds": "P0D"}) == "video_unknown"

    row = {"title": "선크림 추천", "description": "백탁  없어요", "tags_json": '["무기자차"]'}
    text = video_text(row)
    assert "선크림 추천" in text and "백탁 없어요" in text, text   # 공백 축약이 걸려야 한다
    assert "무기자차" not in text, "태그는 판정 텍스트에 넣지 않는다 — trend.py 와 어긋난다"
    assert video_tags(row) == ["무기자차"], "태그는 메타데이터로 보존한다"
    assert video_tags({"tags_json": "not json"}) == []

    rows = mentions_for("d1", text)
    topics = {r["topic_id"] for r in rows}
    assert "백탁" in topics, topics
    assert "선크림" in topics, "trend_use=False 주제도 내보내야 모집단 필터를 만들 수 있다"
    for r in rows:
        assert r["span_start"] >= 0 and r["matched_term"], r
        assert text[r["span_start"]:].lower().startswith(r["matched_term"].lower()), r

    trend_only = {r["topic_id"] for r in rows if r["trend_use"] == "true"}
    assert "선크림" not in trend_only
    print("demo ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dirs", nargs="*", type=Path, help="data/panel/run_* 디렉터리")
    parser.add_argument("--out", default=Path("common"), type=Path)
    parser.add_argument("--demo", action="store_true", help="자체 검사만 실행")
    args = parser.parse_args()

    if args.demo:
        demo()
        return 0
    if not args.run_dirs:
        parser.error("run_dirs 를 하나 이상 지정하거나 --demo 를 쓴다")

    manifest = convert(args.run_dirs, args.out)
    print(json.dumps({k: manifest[k] for k in
                      ("source_runs", "table_counts", "documents_by_content_type", "input_counts")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
