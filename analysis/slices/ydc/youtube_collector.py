#!/usr/bin/env python3
"""YouTube Data API v3 collector for the cosmetics-trend PoC.

Collects:
1) video IDs from keyword searches,
2) video metadata/statistics in batches,
3) top-level comments and optional replies,
4) raw API responses, flat CSV files, and a run manifest.

The API key is read from YOUTUBE_API_KEY in .env or the process environment.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://www.googleapis.com/youtube/v3"
COLLECTOR_VERSION = "1.1.0"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class YouTubeAPIError(RuntimeError):
    """Structured error returned by the YouTube Data API."""

    def __init__(self, status_code: int, reason: str, message: str, payload: Any = None):
        super().__init__(f"YouTube API error {status_code} [{reason}]: {message}")
        self.status_code = status_code
        self.reason = reason
        self.message = message
        self.payload = payload


@dataclass(frozen=True)
class RunConfig:
    queries: list[str]
    published_after: str | None
    published_before: str | None
    window: str
    max_videos_per_query: int
    max_comments_per_video: int
    include_replies: bool
    order: str
    region_code: str
    relevance_language: str
    safe_search: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_api_datetime(value: str | None, *, end_of_day: bool = False) -> str | None:
    """Convert YYYY-MM-DD or an ISO/RFC3339 timestamp to a UTC RFC3339 string."""
    if not value:
        return None

    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        parsed_date = date.fromisoformat(raw)
        parsed_time = dt_time(23, 59, 59) if end_of_day else dt_time(0, 0, 0)
        parsed = datetime.combine(parsed_date, parsed_time, tzinfo=timezone.utc)
    else:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_rfc3339(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_rfc3339(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def next_month_start(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(value.year, value.month + 1, 1, tzinfo=timezone.utc)


def build_windows(
    published_after: str | None,
    published_before: str | None,
    mode: str,
) -> list[dict[str, str | None]]:
    """Build one search range or consecutive calendar-month ranges."""
    if mode == "single":
        return [{"label": "all", "start": published_after, "end": published_before}]
    if mode != "monthly":
        raise ValueError(f"Unsupported window mode: {mode}")
    if not published_after or not published_before:
        raise ValueError("--window monthly requires both --published-after and --published-before.")

    start = parse_rfc3339(published_after)
    end = parse_rfc3339(published_before)
    if start > end:
        raise ValueError("--published-after must not be later than --published-before.")

    windows: list[dict[str, str | None]] = []
    cursor = start
    while cursor <= end:
        window_end = min(end, next_month_start(cursor) - timedelta(seconds=1))
        windows.append(
            {
                "label": cursor.strftime("%Y-%m"),
                "start": to_rfc3339(cursor),
                "end": to_rfc3339(window_end),
            }
        )
        cursor = window_end + timedelta(seconds=1)
    return windows


def estimate_search_calls(query_count: int, window_count: int, max_videos_per_query: int) -> int:
    return query_count * window_count * math.ceil(max_videos_per_query / 50)


def duration_to_seconds(value: str | None) -> int | None:
    """Convert the common YouTube ISO 8601 duration form PT#H#M#S to seconds."""
    if not value:
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def chunks(values: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def author_channel_hash(snippet: dict[str, Any]) -> str | None:
    raw = snippet.get("authorChannelId")
    channel_id = raw.get("value") if isinstance(raw, dict) else None
    if not channel_id:
        return None
    return hashlib.sha256(f"youtube:{channel_id}".encode("utf-8")).hexdigest()[:24]


def load_dotenv_file(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE lines without adding a third-party dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def extract_error_body(body: str) -> tuple[str, str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "httpError", body[:500], None

    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = error.get("message") or body[:500]
    reasons = []
    for item in error.get("errors", []):
        if isinstance(item, dict) and item.get("reason"):
            reasons.append(str(item["reason"]))
    reason = reasons[0] if reasons else "apiError"
    return reason, message, payload


class YouTubeClient:
    def __init__(self, api_key: str, timeout_seconds: int = 30, max_retries: int = 3):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.calls: defaultdict[str, int] = defaultdict(int)

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        clean_params = {key: value for key, value in params.items() if value is not None}
        request_params = {**clean_params, "key": self.api_key}
        url = f"{API_BASE_URL}/{endpoint}?{urlencode(request_params)}"

        for attempt in range(self.max_retries + 1):
            self.calls[endpoint] += 1
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "cosmetics-trend-poc/1.0"})
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                reason, message, payload = extract_error_body(body)
                if exc.code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                    time.sleep(delay)
                    continue
                raise YouTubeAPIError(exc.code, reason, message, payload) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"YouTube API network failure: {exc}") from exc
                time.sleep(2**attempt)
                continue

        raise RuntimeError("Unexpected request loop termination")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def raw_envelope(endpoint: str, params: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "collected_at": utc_now_iso(),
        "request": params,
        "response": response,
        "collector_version": COLLECTOR_VERSION,
    }


def collect_search_hits(
    client: YouTubeClient,
    query: str,
    config: RunConfig,
    raw_path: Path,
    *,
    window_start: str | None,
    window_end: str | None,
    window_label: str,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    page_token: str | None = None

    while len(hits) < config.max_videos_per_query:
        page_size = min(50, config.max_videos_per_query - len(hits))
        params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": page_size,
            "order": config.order,
            "regionCode": config.region_code,
            "relevanceLanguage": config.relevance_language,
            "safeSearch": config.safe_search,
            "publishedAfter": window_start,
            "publishedBefore": window_end,
            "pageToken": page_token,
        }
        clean_params = {key: value for key, value in params.items() if value is not None}
        response = client.get("search", clean_params)
        append_jsonl(raw_path, raw_envelope("search", clean_params, response))

        for item in response.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            snippet = item.get("snippet", {})
            hits.append(
                {
                    "video_id": video_id,
                    "query": query,
                    "search_rank": len(hits) + 1,
                    "search_title": snippet.get("title"),
                    "search_published_at": snippet.get("publishedAt"),
                    "window_label": window_label,
                    "window_start": window_start,
                    "window_end": window_end,
                }
            )
            if len(hits) >= config.max_videos_per_query:
                break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return hits


def collect_video_resources(
    client: YouTubeClient,
    video_ids: Sequence[str],
    raw_path: Path,
) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for batch in chunks(video_ids, 50):
        params = {
            "part": "snippet,statistics,contentDetails,status,paidProductPlacementDetails",
            "id": ",".join(batch),
        }
        response = client.get("videos", params)
        append_jsonl(raw_path, raw_envelope("videos", params, response))
        resources.extend(response.get("items", []))
    return resources


def flatten_video(
    resource: dict[str, Any],
    matched_queries: Sequence[str],
    matched_windows: Sequence[str],
    search_ranks: Sequence[int],
    collected_at: str,
) -> dict[str, Any]:
    video_id = resource.get("id")
    snippet = resource.get("snippet", {})
    statistics = resource.get("statistics", {})
    content = resource.get("contentDetails", {})
    status = resource.get("status", {})
    paid = resource.get("paidProductPlacementDetails", {})
    thumbnails = snippet.get("thumbnails", {})
    best_thumb = thumbnails.get("maxres") or thumbnails.get("standard") or thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
    duration = content.get("duration")

    return {
        "source": "youtube",
        "source_item_id": video_id,
        "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
        "matched_queries": " | ".join(matched_queries),
        "matched_windows": " | ".join(matched_windows),
        "search_hit_count": len(matched_queries),
        "best_search_rank": min(search_ranks) if search_ranks else None,
        "published_at": snippet.get("publishedAt"),
        "analysis_month": (snippet.get("publishedAt") or "")[:7] or None,
        "collected_at": collected_at,
        "channel_id": snippet.get("channelId"),
        "channel_title": snippet.get("channelTitle"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "tags_json": json.dumps(snippet.get("tags", []), ensure_ascii=False),
        "category_id": snippet.get("categoryId"),
        "default_language": snippet.get("defaultLanguage"),
        "default_audio_language": snippet.get("defaultAudioLanguage"),
        "thumbnail_url": best_thumb.get("url"),
        "duration_iso8601": duration,
        "duration_seconds": duration_to_seconds(duration),
        "caption_available": content.get("caption") == "true",
        "licensed_content": content.get("licensedContent"),
        "privacy_status": status.get("privacyStatus"),
        "made_for_kids": status.get("madeForKids"),
        "view_count": to_int(statistics.get("viewCount")),
        "like_count": to_int(statistics.get("likeCount")),
        "comment_count": to_int(statistics.get("commentCount")),
        "favorite_count": to_int(statistics.get("favoriteCount")),
        "has_paid_product_placement": paid.get("hasPaidProductPlacement"),
        "collector_version": COLLECTOR_VERSION,
    }


def flatten_comment(
    comment: dict[str, Any],
    *,
    video_id: str,
    matched_queries: Sequence[str],
    matched_windows: Sequence[str],
    collected_at: str,
    thread_id: str | None,
    parent_comment_id: str | None,
    is_reply: bool,
    total_reply_count: int | None,
) -> dict[str, Any]:
    snippet = comment.get("snippet", {})
    return {
        "source": "youtube_comment",
        "comment_id": comment.get("id"),
        "video_id": video_id,
        "canonical_video_url": f"https://www.youtube.com/watch?v={video_id}",
        "thread_id": thread_id,
        "parent_comment_id": parent_comment_id,
        "is_reply": is_reply,
        "text": snippet.get("textDisplay") or snippet.get("textOriginal"),
        "like_count": to_int(snippet.get("likeCount")),
        "published_at": snippet.get("publishedAt"),
        "updated_at": snippet.get("updatedAt"),
        "author_channel_hash": author_channel_hash(snippet),
        "total_reply_count": total_reply_count,
        "matched_queries": " | ".join(matched_queries),
        "matched_windows": " | ".join(matched_windows),
        "collected_at": collected_at,
        "collector_version": COLLECTOR_VERSION,
    }


def collect_replies(
    client: YouTubeClient,
    *,
    video_id: str,
    parent_comment_id: str,
    thread_id: str,
    matched_queries: Sequence[str],
    matched_windows: Sequence[str],
    collected_at: str,
    limit: int,
    raw_path: Path,
) -> list[dict[str, Any]]:
    replies: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(replies) < limit:
        params: dict[str, Any] = {
            "part": "snippet",
            "parentId": parent_comment_id,
            "maxResults": min(100, limit - len(replies)),
            "textFormat": "plainText",
            "pageToken": page_token,
        }
        clean_params = {key: value for key, value in params.items() if value is not None}
        response = client.get("comments", clean_params)
        append_jsonl(raw_path, raw_envelope("comments", clean_params, response))
        for item in response.get("items", []):
            replies.append(
                flatten_comment(
                    item,
                    video_id=video_id,
                    matched_queries=matched_queries,
                    matched_windows=matched_windows,
                    collected_at=collected_at,
                    thread_id=thread_id,
                    parent_comment_id=parent_comment_id,
                    is_reply=True,
                    total_reply_count=None,
                )
            )
            if len(replies) >= limit:
                break
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return replies


def collect_comments_for_video(
    client: YouTubeClient,
    *,
    video_id: str,
    matched_queries: Sequence[str],
    matched_windows: Sequence[str],
    collected_at: str,
    limit: int,
    include_replies: bool,
    raw_path: Path,
    order: str = "time",
) -> list[dict[str, Any]]:
    """order: "time"은 최신순, "relevance"는 유튜브 참여도 순.

    패널 수집은 relevance를 쓴다. 3년 전 영상에서 최신순을 뽑으면 최근 잡담이
    올라오지만, relevance는 좋아요·답글이 붙은 실제 사용 후기가 위로 온다.
    relevance는 좋아요 순이 아니라 비공개 알고리즘이므로, 좋아요로 재정렬하려면
    저장된 like_count를 쓴다.
    """
    rows: list[dict[str, Any]] = []
    page_token: str | None = None

    while len(rows) < limit:
        params: dict[str, Any] = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(100, limit - len(rows)),
            "order": order,
            "textFormat": "plainText",
            "pageToken": page_token,
        }
        clean_params = {key: value for key, value in params.items() if value is not None}
        response = client.get("commentThreads", clean_params)
        append_jsonl(raw_path, raw_envelope("commentThreads", clean_params, response))

        for thread in response.get("items", []):
            thread_snippet = thread.get("snippet", {})
            top_comment = thread_snippet.get("topLevelComment", {})
            top_comment_id = top_comment.get("id")
            total_reply_count = to_int(thread_snippet.get("totalReplyCount")) or 0
            rows.append(
                flatten_comment(
                    top_comment,
                    video_id=video_id,
                    matched_queries=matched_queries,
                    matched_windows=matched_windows,
                    collected_at=collected_at,
                    thread_id=thread.get("id"),
                    parent_comment_id=None,
                    is_reply=False,
                    total_reply_count=total_reply_count,
                )
            )
            if len(rows) >= limit:
                break

            if include_replies and total_reply_count > 0 and top_comment_id:
                remaining = limit - len(rows)
                rows.extend(
                    collect_replies(
                        client,
                        video_id=video_id,
                        parent_comment_id=top_comment_id,
                        thread_id=thread.get("id"),
                        matched_queries=matched_queries,
                        matched_windows=matched_windows,
                        collected_at=collected_at,
                        limit=remaining,
                        raw_path=raw_path,
                    )
                )
            if len(rows) >= limit:
                break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # A reply call can reach the remaining limit exactly; keep a hard upper bound.
    return rows[:limit]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_queries(cli_queries: Sequence[str], queries_file: str | None) -> list[str]:
    values = list(cli_queries)
    if queries_file:
        path = Path(queries_file)
        if not path.exists():
            raise FileNotFoundError(f"Queries file not found: {path}")
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                values.append(stripped)
    queries = unique_preserving_order(values)
    if not queries:
        raise ValueError("Provide at least one --query or --queries-file value.")
    return queries


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect YouTube videos and comments for trend analysis.")
    parser.add_argument("--query", action="append", default=[], help="Search query. Repeat this option for multiple queries.")
    parser.add_argument("--queries-file", help="UTF-8 text file containing one search query per line.")
    parser.add_argument("--published-after", help="YYYY-MM-DD or RFC3339 UTC timestamp.")
    parser.add_argument("--published-before", help="YYYY-MM-DD or RFC3339 UTC timestamp.")
    parser.add_argument(
        "--window",
        choices=["single", "monthly"],
        default="single",
        help="Split the date range into calendar-month searches (default: single).",
    )
    parser.add_argument(
        "--search-call-limit",
        type=int,
        default=100,
        help="Stop if estimated search calls exceed this value (default: 100).",
    )
    parser.add_argument(
        "--allow-search-call-overflow",
        action="store_true",
        help="Allow estimated search calls to exceed --search-call-limit.",
    )
    parser.add_argument("--max-videos-per-query", type=int, default=25)
    parser.add_argument("--max-comments-per-video", type=int, default=100, help="0 skips comment collection.")
    parser.add_argument("--include-replies", action="store_true", help="Fetch all available replies until the per-video comment limit is reached.")
    parser.add_argument("--order", choices=["date", "relevance", "viewCount", "rating", "title"], default="date")
    parser.add_argument("--region-code", default="KR")
    parser.add_argument("--language", default="ko", dest="relevance_language")
    parser.add_argument("--safe-search", choices=["moderate", "none", "strict"], default="moderate")
    parser.add_argument("--output-dir", default="data/youtube")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-retries", type=int, default=3)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.max_videos_per_query < 1:
        raise ValueError("--max-videos-per-query must be at least 1.")
    if args.max_comments_per_video < 0:
        raise ValueError("--max-comments-per-video cannot be negative.")
    if args.search_call_limit < 1:
        raise ValueError("--search-call-limit must be at least 1.")
    if len(args.region_code) != 2:
        raise ValueError("--region-code must be a two-letter country code, such as KR.")


def run(argv: Sequence[str] | None = None) -> Path:
    load_dotenv_file()
    args = parse_args(argv)
    validate_args(args)
    queries = load_queries(args.query, args.queries_file)
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is missing. Copy .env.example to .env and add the API key.")

    config = RunConfig(
        queries=queries,
        published_after=normalize_api_datetime(args.published_after),
        published_before=normalize_api_datetime(args.published_before, end_of_day=True),
        window=args.window,
        max_videos_per_query=args.max_videos_per_query,
        max_comments_per_video=args.max_comments_per_video,
        include_replies=args.include_replies,
        order=args.order,
        region_code=args.region_code.upper(),
        relevance_language=args.relevance_language,
        safe_search=args.safe_search,
    )

    windows = build_windows(config.published_after, config.published_before, config.window)
    estimated_search_calls = estimate_search_calls(
        len(config.queries), len(windows), config.max_videos_per_query
    )
    if estimated_search_calls > args.search_call_limit and not args.allow_search_call_overflow:
        raise ValueError(
            f"Estimated search calls ({estimated_search_calls}) exceed --search-call-limit "
            f"({args.search_call_limit}). Reduce queries/windows/videos, raise the limit after "
            "checking your Google Cloud quota, or add --allow-search-call-overflow."
        )
    print(
        f"[plan] windows: {len(windows)}, queries: {len(config.queries)}, "
        f"estimated search calls: {estimated_search_calls}"
    )

    run_started_at = utc_now_iso()
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    run_dir = Path(args.output_dir) / run_id
    raw_dir = run_dir / "raw"
    processed_dir = run_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    search_raw = raw_dir / "search_responses.jsonl"
    videos_raw = raw_dir / "video_responses.jsonl"
    comments_raw = raw_dir / "comment_responses.jsonl"
    errors_path = run_dir / "errors.jsonl"

    client = YouTubeClient(api_key, args.timeout_seconds, args.max_retries)
    search_hits: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    total_searches = len(windows) * len(config.queries)
    search_number = 0
    for window in windows:
        for query in config.queries:
            search_number += 1
            print(f"[search {search_number}/{total_searches}] {window['label']} | {query}")
            search_hits.extend(
                collect_search_hits(
                    client,
                    query,
                    config,
                    search_raw,
                    window_start=window["start"],
                    window_end=window["end"],
                    window_label=str(window["label"]),
                )
            )

    queries_by_video: defaultdict[str, list[str]] = defaultdict(list)
    windows_by_video: defaultdict[str, list[str]] = defaultdict(list)
    ranks_by_video: defaultdict[str, list[int]] = defaultdict(list)
    for hit in search_hits:
        video_id = hit["video_id"]
        if hit["query"] not in queries_by_video[video_id]:
            queries_by_video[video_id].append(hit["query"])
        if hit["window_label"] not in windows_by_video[video_id]:
            windows_by_video[video_id].append(hit["window_label"])
        ranks_by_video[video_id].append(hit["search_rank"])

    video_ids = list(queries_by_video)
    print(f"[videos] unique IDs: {len(video_ids)}")
    resources = collect_video_resources(client, video_ids, videos_raw)
    collected_at = utc_now_iso()
    video_rows = [
        flatten_video(
            resource,
            queries_by_video[resource["id"]],
            windows_by_video[resource["id"]],
            ranks_by_video[resource["id"]],
            collected_at,
        )
        for resource in resources
        if resource.get("id")
    ]

    comment_rows: list[dict[str, Any]] = []
    if config.max_comments_per_video > 0:
        for index, video in enumerate(video_rows, start=1):
            video_id = str(video["source_item_id"])
            print(f"[comments {index}/{len(video_rows)}] {video_id}")
            try:
                comment_rows.extend(
                    collect_comments_for_video(
                        client,
                        video_id=video_id,
                        matched_queries=queries_by_video[video_id],
                        matched_windows=windows_by_video[video_id],
                        collected_at=collected_at,
                        limit=config.max_comments_per_video,
                        include_replies=config.include_replies,
                        raw_path=comments_raw,
                    )
                )
            except YouTubeAPIError as exc:
                error_record = {
                    "video_id": video_id,
                    "status_code": exc.status_code,
                    "reason": exc.reason,
                    "message": exc.message,
                    "collected_at": utc_now_iso(),
                }
                errors.append(error_record)
                append_jsonl(errors_path, error_record)
                if exc.reason in {"commentsDisabled", "videoNotFound", "forbidden"}:
                    print(f"  skipped: {exc.reason}")
                    continue
                raise

    # De-duplicate comments defensively across paginated responses.
    unique_comments: dict[str, dict[str, Any]] = {}
    for row in comment_rows:
        comment_id = row.get("comment_id")
        if comment_id:
            unique_comments[str(comment_id)] = row
    comment_rows = list(unique_comments.values())

    write_csv(processed_dir / "videos.csv", video_rows)
    write_csv(processed_dir / "comments.csv", comment_rows)

    manifest = {
        "run_id": run_id,
        "run_started_at": run_started_at,
        "run_finished_at": utc_now_iso(),
        "config": asdict(config),
        "windows": windows,
        "estimated_search_calls": estimated_search_calls,
        "counts": {
            "windows": len(windows),
            "search_hits": len(search_hits),
            "unique_video_ids": len(video_ids),
            "video_rows": len(video_rows),
            "comment_rows": len(comment_rows),
            "skipped_or_failed_videos": len(errors),
        },
        "api_calls": dict(sorted(client.calls.items())),
        "quota_note": {
            "search_calls": client.calls.get("search", 0),
            "general_read_units_estimate": sum(
                client.calls.get(endpoint, 0) for endpoint in ("videos", "commentThreads", "comments")
            ),
            "warning": "Quota policy can change; confirm current limits in Google Cloud Console and official YouTube API documentation.",
        },
        "outputs": {
            "videos_csv": str(processed_dir / "videos.csv"),
            "comments_csv": str(processed_dir / "comments.csv"),
            "raw_directory": str(raw_dir),
            "errors_jsonl": str(errors_path),
        },
        "collector_version": COLLECTOR_VERSION,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone: {run_dir.resolve()}")
    return run_dir


def main() -> int:
    try:
        run()
    except (ValueError, FileNotFoundError, RuntimeError, YouTubeAPIError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
