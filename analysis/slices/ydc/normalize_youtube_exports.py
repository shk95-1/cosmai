#!/usr/bin/env python3
"""Normalize YouTube collector CSV exports into analysis-ready relational tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Iterable


ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE_RE = re.compile(r"\s+")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_list = list(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(row_list)
    return len(row_list)


def normalized_text(value: str | None) -> str:
    """NFKC-normalize, decode entities, remove controls, and collapse whitespace."""
    if not value:
        return ""
    text = html.unescape(value)
    text = unicodedata.normalize("NFKC", text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = CONTROL_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def nullable_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def nullable_bool(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    lowered = value.strip().lower()
    if lowered == "true":
        return 1
    if lowered == "false":
        return 0
    raise ValueError(f"Invalid boolean value: {value!r}")


def split_pipe(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def stable_id(prefix: str, value: str, length: int = 12) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def unique_by(rows: Iterable[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    result: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        result.setdefault(key, row)
    return list(result.values())


def normalize(videos_path: Path, comments_path: Path, output_dir: Path) -> dict[str, Any]:
    source_videos = read_csv(videos_path)
    source_comments = read_csv(comments_path)
    if not source_videos:
        raise ValueError("The videos CSV has no data rows.")

    video_ids = [row["source_item_id"] for row in source_videos]
    comment_ids = [row["comment_id"] for row in source_comments]
    if len(video_ids) != len(set(video_ids)):
        raise ValueError("Duplicate source_item_id values exist in the videos CSV.")
    if len(comment_ids) != len(set(comment_ids)):
        raise ValueError("Duplicate comment_id values exist in the comments CSV.")

    video_id_set = set(video_ids)
    orphan_comment_ids = [row["comment_id"] for row in source_comments if row["video_id"] not in video_id_set]
    if orphan_comment_ids:
        raise ValueError(f"Comments reference missing videos: {len(orphan_comment_ids)} row(s).")

    channel_rows = unique_by(
        (
            {
                "channel_id": row["channel_id"],
                "channel_title": normalized_text(row.get("channel_title")),
            }
            for row in source_videos
            if row.get("channel_id")
        ),
        ("channel_id",),
    )

    video_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    query_by_id: OrderedDict[str, dict[str, str]] = OrderedDict()
    query_match_rows: list[dict[str, Any]] = []
    tag_by_id: OrderedDict[str, dict[str, str]] = OrderedDict()
    video_tag_rows: list[dict[str, str]] = []

    for row in source_videos:
        video_id = row["source_item_id"]
        video_rows.append(
            {
                "video_id": video_id,
                "channel_id": row.get("channel_id") or None,
                "source": row.get("source"),
                "canonical_url": row.get("canonical_url"),
                "title_raw": row.get("title"),
                "title_normalized": normalized_text(row.get("title")),
                "description_raw": row.get("description"),
                "description_normalized": normalized_text(row.get("description")),
                "published_at": row.get("published_at"),
                "analysis_month": row.get("analysis_month"),
                "category_id": row.get("category_id") or None,
                "default_language": row.get("default_language") or None,
                "default_audio_language": row.get("default_audio_language") or None,
                "thumbnail_url": row.get("thumbnail_url") or None,
                "duration_iso8601": row.get("duration_iso8601") or None,
                "duration_seconds": nullable_int(row.get("duration_seconds")),
                "caption_available": nullable_bool(row.get("caption_available")),
                "licensed_content": nullable_bool(row.get("licensed_content")),
                "privacy_status": row.get("privacy_status") or None,
                "made_for_kids": nullable_bool(row.get("made_for_kids")),
                "has_paid_product_placement": nullable_bool(row.get("has_paid_product_placement")),
            }
        )
        metric_rows.append(
            {
                "video_id": video_id,
                "collected_at": row.get("collected_at"),
                "view_count": nullable_int(row.get("view_count")),
                "like_count": nullable_int(row.get("like_count")),
                "comment_count": nullable_int(row.get("comment_count")),
                "favorite_count": nullable_int(row.get("favorite_count")),
            }
        )

        queries = split_pipe(row.get("matched_queries"))
        windows = split_pipe(row.get("matched_windows"))
        for query in queries:
            query_id = stable_id("query", query)
            query_by_id.setdefault(query_id, {"query_id": query_id, "query_text": query})
            for window in windows:
                query_match_rows.append(
                    {
                        "video_id": video_id,
                        "query_id": query_id,
                        "window_month": window,
                    }
                )

        raw_tags = row.get("tags_json") or "[]"
        try:
            tags = json.loads(raw_tags)
        except json.JSONDecodeError:
            tags = []
        if not isinstance(tags, list):
            tags = []
        for raw_tag in tags:
            if not isinstance(raw_tag, str):
                continue
            clean_tag = normalized_text(raw_tag)
            if not clean_tag:
                continue
            tag_key = clean_tag.casefold()
            tag_id = stable_id("tag", tag_key)
            tag_by_id.setdefault(tag_id, {"tag_id": tag_id, "tag_text_normalized": clean_tag})
            video_tag_rows.append(
                {
                    "video_id": video_id,
                    "tag_id": tag_id,
                    "tag_text_raw": raw_tag,
                }
            )

    query_match_rows = unique_by(query_match_rows, ("video_id", "query_id", "window_month"))
    video_tag_rows = unique_by(video_tag_rows, ("video_id", "tag_id"))

    comment_rows = [
        {
            "comment_id": row["comment_id"],
            "video_id": row["video_id"],
            "thread_id": row.get("thread_id") or None,
            "parent_comment_id": row.get("parent_comment_id") or None,
            "is_reply": nullable_bool(row.get("is_reply")),
            "text_raw": row.get("text"),
            "text_normalized": normalized_text(row.get("text")),
            "like_count": nullable_int(row.get("like_count")),
            "published_at": row.get("published_at"),
            "updated_at": row.get("updated_at"),
            "author_channel_hash": row.get("author_channel_hash") or None,
            "total_reply_count": nullable_int(row.get("total_reply_count")),
            "collected_at": row.get("collected_at"),
        }
        for row in source_comments
    ]

    collected_values = sorted({row.get("collected_at", "") for row in source_videos if row.get("collected_at")})
    collected_at = collected_values[0] if len(collected_values) == 1 else " | ".join(collected_values)
    collector_versions = sorted({row.get("collector_version", "") for row in source_videos if row.get("collector_version")})
    run_id = "run_" + re.sub(r"\D", "", collected_values[0])[:14] if collected_values else "run_unknown"
    run_rows = [
        {
            "run_id": run_id,
            "source": "youtube",
            "collected_at": collected_at,
            "collector_version": " | ".join(collector_versions),
            "source_video_rows": len(source_videos),
            "source_comment_rows": len(source_comments),
        }
    ]

    tables = {
        "collection_runs.csv": (
            ["run_id", "source", "collected_at", "collector_version", "source_video_rows", "source_comment_rows"],
            run_rows,
        ),
        "channels.csv": (["channel_id", "channel_title"], channel_rows),
        "videos.csv": (
            [
                "video_id", "channel_id", "source", "canonical_url", "title_raw", "title_normalized",
                "description_raw", "description_normalized", "published_at", "analysis_month", "category_id",
                "default_language", "default_audio_language", "thumbnail_url", "duration_iso8601", "duration_seconds",
                "caption_available", "licensed_content", "privacy_status", "made_for_kids", "has_paid_product_placement",
            ],
            video_rows,
        ),
        "video_metrics.csv": (
            ["video_id", "collected_at", "view_count", "like_count", "comment_count", "favorite_count"],
            metric_rows,
        ),
        "search_queries.csv": (["query_id", "query_text"], list(query_by_id.values())),
        "video_query_matches.csv": (["video_id", "query_id", "window_month"], query_match_rows),
        "comments.csv": (
            [
                "comment_id", "video_id", "thread_id", "parent_comment_id", "is_reply", "text_raw",
                "text_normalized", "like_count", "published_at", "updated_at", "author_channel_hash",
                "total_reply_count", "collected_at",
            ],
            comment_rows,
        ),
        "tags.csv": (["tag_id", "tag_text_normalized"], list(tag_by_id.values())),
        "video_tags.csv": (["video_id", "tag_id", "tag_text_raw"], video_tag_rows),
    }

    table_counts: dict[str, int] = {}
    for filename, (fieldnames, rows) in tables.items():
        table_counts[filename] = write_csv(output_dir / filename, fieldnames, rows)

    qa_metrics = [
        {"metric": "source_video_rows", "value": len(source_videos), "status": "PASS", "note": "원본 영상 행 수"},
        {"metric": "normalized_video_rows", "value": len(video_rows), "status": "PASS", "note": "video_id 기준 1행"},
        {"metric": "duplicate_video_ids", "value": len(video_ids) - len(set(video_ids)), "status": "PASS", "note": "0이어야 함"},
        {"metric": "source_comment_rows", "value": len(source_comments), "status": "PASS", "note": "원본 댓글 행 수"},
        {"metric": "normalized_comment_rows", "value": len(comment_rows), "status": "PASS", "note": "comment_id 기준 1행"},
        {"metric": "duplicate_comment_ids", "value": len(comment_ids) - len(set(comment_ids)), "status": "PASS", "note": "0이어야 함"},
        {"metric": "orphan_comments", "value": len(orphan_comment_ids), "status": "PASS", "note": "존재하지 않는 video_id 참조"},
        {"metric": "channels", "value": len(channel_rows), "status": "INFO", "note": "고유 채널 수"},
        {"metric": "queries", "value": len(query_by_id), "status": "INFO", "note": "고유 검색어 수"},
        {"metric": "video_query_matches", "value": len(query_match_rows), "status": "INFO", "note": "다대다 관계 행 수"},
        {"metric": "tags", "value": len(tag_by_id), "status": "INFO", "note": "정규화된 고유 태그 수"},
        {"metric": "video_tag_matches", "value": len(video_tag_rows), "status": "INFO", "note": "영상-태그 관계 행 수"},
    ]
    table_counts["data_quality.csv"] = write_csv(
        output_dir / "data_quality.csv", ["metric", "value", "status", "note"], qa_metrics
    )

    manifest = {
        "inputs": {"videos": str(videos_path), "comments": str(comments_path)},
        "output_dir": str(output_dir),
        "table_counts": table_counts,
        "primary_keys": {
            "collection_runs.csv": ["run_id"],
            "channels.csv": ["channel_id"],
            "videos.csv": ["video_id"],
            "video_metrics.csv": ["video_id", "collected_at"],
            "search_queries.csv": ["query_id"],
            "video_query_matches.csv": ["video_id", "query_id", "window_month"],
            "comments.csv": ["comment_id"],
            "tags.csv": ["tag_id"],
            "video_tags.csv": ["video_id", "tag_id"],
        },
        "foreign_keys": {
            "videos.channel_id": "channels.channel_id",
            "video_metrics.video_id": "videos.video_id",
            "video_query_matches.video_id": "videos.video_id",
            "video_query_matches.query_id": "search_queries.query_id",
            "comments.video_id": "videos.video_id",
            "video_tags.video_id": "videos.video_id",
            "video_tags.tag_id": "tags.tag_id",
        },
        "normalization_rules": [
            "원문 텍스트는 *_raw에 보존",
            "분석용 텍스트는 HTML 엔터티 해제, Unicode NFKC, 제어문자 제거, 공백 축약",
            "불리언은 1/0, 결측은 빈 값",
            "숫자 결측은 0으로 대체하지 않음",
            "검색어와 태그는 다대다 연결 테이블로 분리",
            "수집 시점에 변하는 통계는 video_metrics로 분리",
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize YouTube collector CSV exports.")
    parser.add_argument("--videos", required=True, type=Path)
    parser.add_argument("--comments", required=True, type=Path)
    parser.add_argument("--output-dir", default=Path("normalized"), type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = normalize(args.videos, args.comments, args.output_dir)
    print(json.dumps(manifest["table_counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
