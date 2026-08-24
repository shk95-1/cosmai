#!/usr/bin/env python3
"""시드 영상에서 채널을 뽑아 각 채널의 업로드를 전수 수집한다 (고정 패널 방식).

왜 search가 아니라 playlistItems인가:
- search.list는 쿼리당 약 500건에서 끊기고 콜당 100유닛이다. 재현율도 낮다
  (손으로 고른 45편 중 검색이 잡은 것은 수집 구간 내 6/20 = 30%).
- search 순위는 "오늘의" 참여도에 의존하므로 과거 구간을 조회하면 그 후 성공한
  영상만 잡힌다(생존 편향). 시계열 질문에 쓸 수 없다.
- 채널 업로드 플레이리스트는 전수이고 50편당 1유닛이다. 망한 영상도 포함되므로
  분기별 문서 수 분모가 편향 없이 만들어진다.

한계: 모집단은 "이 시드 채널들"이다. 전체 YouTube가 아니다. 고정 패널 설계이므로
화자 구성 변화가 트렌드로 위장되지 않는 대신, 패널 밖 신규 등장은 보이지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator, Sequence

from topics import match_topics
from youtube_collector import (
    YouTubeAPIError,
    YouTubeClient,
    collect_comments_for_video,
    append_jsonl,
    chunks,
    collect_video_resources,
    flatten_video,
    load_dotenv_file,
    parse_rfc3339,
    raw_envelope,
    unique_preserving_order,
    utc_now_iso,
    write_csv,
)

# search=100유닛, 그 외 읽기 엔드포인트=1유닛.
UNIT_COST = {"search": 100}
VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def parse_seed_file(path: Path) -> list[str]:
    """시드 파일에서 영상 ID를 뽑는다. `#` 주석줄과 URL 뒤 텍스트는 무시한다."""
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = VIDEO_ID_RE.search(line)
        if match:
            ids.append(match.group(1))
    return unique_preserving_order(ids)


def parse_channels_csv(path: Path) -> list[dict[str, str]]:
    """채널 패널 CSV를 읽는다. channel_id와 panel_role만 필수다.

    panel_role은 트렌드 분모에 넣을 채널(product)과 의학·성분 해설로 분리할
    채널(expert)을 가른다. 수집은 둘 다 하고 집계에서 필터한다. 단계적으로
    채널을 추가하면 분모가 중간에 바뀌어 시계열이 끊기므로 한 번에 다 받는다.
    """
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    missing = [r for r in rows if not r.get("channel_id")]
    if missing:
        raise SystemExit(f"{path}: channel_id가 빈 행이 {len(missing)}개 있습니다.")
    return rows


def spent_units(client: YouTubeClient) -> int:
    return sum(count * UNIT_COST.get(endpoint, 1) for endpoint, count in client.calls.items())


def resolve_uploads_playlists(
    client: YouTubeClient,
    channel_ids: Sequence[str],
    raw_path: Path,
) -> list[dict[str, Any]]:
    """채널별 업로드 플레이리스트 ID와 구독자·영상 수를 받는다. 50채널당 1유닛."""
    rows: list[dict[str, Any]] = []
    for batch in chunks(list(channel_ids), 50):
        params = {"part": "snippet,contentDetails,statistics", "id": ",".join(batch)}
        response = client.get("channels", params)
        append_jsonl(raw_path, raw_envelope("channels", params, response))
        for item in response.get("items", []):
            stats = item.get("statistics", {})
            rows.append(
                {
                    "channel_id": item.get("id"),
                    "channel_title": item.get("snippet", {}).get("title"),
                    "uploads_playlist_id": item.get("contentDetails", {})
                    .get("relatedPlaylists", {})
                    .get("uploads"),
                    "channel_published_at": item.get("snippet", {}).get("publishedAt"),
                    "subscriber_count": stats.get("subscriberCount"),
                    "video_count": stats.get("videoCount"),
                    "hidden_subscriber_count": stats.get("hiddenSubscriberCount"),
                }
            )
    return rows


def iter_uploads(
    client: YouTubeClient,
    playlist_id: str,
    published_after: str,
    raw_path: Path,
) -> Iterator[tuple[str, str]]:
    """업로드 플레이리스트를 최신순으로 훑어 (video_id, published_at)을 낸다.

    업로드 플레이리스트는 최신순이므로 cutoff보다 오래된 항목이 나오면 멈춘다.
    ponytail: 최신순 가정에 의존한다. 순서가 뒤섞인 채널이 있으면 조기 종료로
    일부를 놓친다. 그 경우 --no-early-stop으로 전체를 훑어라.
    """
    cutoff = parse_rfc3339(published_after)
    page_token: str | None = None
    while True:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "pageToken": page_token,
        }
        response = client.get("playlistItems", params)
        append_jsonl(raw_path, raw_envelope("playlistItems", params, response))
        items = response.get("items", [])
        for item in items:
            details = item.get("contentDetails", {})
            video_id = details.get("videoId")
            published_at = details.get("videoPublishedAt")
            if not video_id or not published_at:
                continue  # 비공개·삭제된 영상은 videoPublishedAt이 없다
            if parse_rfc3339(published_at) < cutoff:
                return
            yield video_id, published_at
        page_token = response.get("nextPageToken")
        if not page_token:
            return


def run(argv: Sequence[str] | None = None) -> Path:
    args = parse_args(argv)
    load_dotenv_file()
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        raise SystemExit("YOUTUBE_API_KEY가 없습니다. .env를 확인하세요.")

    run_id = "run_" + utc_now_iso().replace("-", "").replace(":", "").replace("Z", "Z")
    run_dir = Path(args.output_dir) / run_id
    raw_dir = run_dir / "raw"
    processed_dir = run_dir / "processed"
    collected_at = utc_now_iso()
    client = YouTubeClient(api_key)

    seed_video_ids = parse_seed_file(Path(args.seed_videos)) if args.seed_videos else []
    channel_ids = list(args.channel_id or [])
    panel_meta: dict[str, dict[str, str]] = {}

    if args.channels_csv:
        panel_rows = parse_channels_csv(Path(args.channels_csv))
        panel_meta = {r["channel_id"]: r for r in panel_rows}
        channel_ids += list(panel_meta)
        roles: dict[str, int] = {}
        for r in panel_rows:
            role = r.get("panel_role") or "unset"
            roles[role] = roles.get(role, 0) + 1
        print(f"[panel] {args.channels_csv}에서 채널 {len(panel_rows)}개 " + ", ".join(f"{k} {v}" for k, v in sorted(roles.items())))

    if seed_video_ids:
        print(f"[seed] 시드 영상 {len(seed_video_ids)}편에서 채널 추출")
        seed_resources = collect_video_resources(client, seed_video_ids, raw_dir / "seed_videos.jsonl")
        found = {r.get("id") for r in seed_resources}
        missing = [v for v in seed_video_ids if v not in found]
        channel_ids += [r.get("snippet", {}).get("channelId") for r in seed_resources]
        if missing:
            print(f"[seed] 조회 실패 {len(missing)}편 (삭제·비공개 가능): {missing}")

    channel_ids = unique_preserving_order([c for c in channel_ids if c])
    if not channel_ids:
        raise SystemExit("채널을 하나도 못 찾았습니다. --seed-videos 또는 --channel-id를 확인하세요.")
    print(f"[seed] 고유 채널 {len(channel_ids)}개 / 소모 {spent_units(client)}유닛")

    channels = resolve_uploads_playlists(client, channel_ids, raw_dir / "channels.jsonl")
    for channel in channels:
        meta = panel_meta.get(channel["channel_id"], {})
        channel["panel_role"] = meta.get("panel_role", "unset")
        channel["team_role"] = meta.get("team_role", "")
    write_csv(processed_dir / "channels.csv", channels)
    total_uploads = sum(int(c["video_count"]) for c in channels if (c.get("video_count") or "").isdigit())
    print(f"[channels] {len(channels)}개 해석됨. 전체 업로드 합계 {total_uploads:,}편")

    estimate = spent_units(client) + -(-total_uploads // 50) + -(-total_uploads // 50)
    print(f"[quota] 최악 추정 {estimate:,}유닛 (한도 {args.max_units:,})")
    if estimate > args.max_units:
        raise SystemExit(
            f"추정 {estimate:,}유닛이 --max-units {args.max_units:,}를 넘습니다. "
            "--published-after를 좁히거나 한도를 올리세요."
        )

    seen: dict[str, str] = {}
    per_channel: list[dict[str, Any]] = []
    for channel in channels:
        playlist_id = channel.get("uploads_playlist_id")
        if not playlist_id:
            print(f"[uploads] {channel['channel_title']}: 업로드 플레이리스트 없음, 건너뜀")
            continue
        before = len(seen)
        for video_id, published_at in iter_uploads(
            client, playlist_id, args.published_after, raw_dir / "playlist_items.jsonl"
        ):
            seen.setdefault(video_id, published_at)
        gained = len(seen) - before
        per_channel.append({"channel_id": channel["channel_id"], "channel_title": channel["channel_title"], "videos_in_window": gained})
        print(f"[uploads] {channel['channel_title']}: {gained}편 (누적 {len(seen)}, {spent_units(client)}유닛)")

    video_ids = sorted(seen)
    print(f"[videos] 고유 영상 {len(video_ids)}편 상세 조회")
    resources = collect_video_resources(client, video_ids, raw_dir / "video_responses.jsonl")
    rows = [
        flatten_video(resource, [], ["panel"], [], collected_at)
        for resource in resources
    ]
    rows.sort(key=lambda r: (r.get("published_at") or "", r.get("source_item_id") or ""))
    write_csv(processed_dir / "videos.csv", rows)
    write_csv(processed_dir / "channel_yield.csv", per_channel)

    # 댓글 단계. 영상 저장이 끝난 뒤에만 시작한다.
    # commentThreads는 영상 1편당 1유닛이라 영상 목록 수집보다 50배 비싸다.
    # 여기서 쿼터가 터져도 videos.csv는 이미 디스크에 있으므로 댓글만 다시 받으면 된다.
    comment_rows: list[dict[str, Any]] = []
    comment_errors: list[dict[str, Any]] = []
    matched: list[tuple[str, list[str]]] = []
    if args.comments > 0:
        for row in rows:
            topics = match_topics(f"{row.get('title') or ''} {row.get('description') or ''}")
            if topics:
                matched.append((str(row["source_item_id"]), topics))
        estimate = spent_units(client) + len(matched)
        pct = (100 * len(matched) / len(rows)) if rows else 0
        print(f"[comments] 주제 매칭 {len(matched)}/{len(rows)}편 ({pct:.1f}%), 추정 {estimate:,}유닛")
        if estimate > args.max_units:
            raise SystemExit(
                f"댓글 추정 {estimate:,}유닛이 --max-units {args.max_units:,}를 넘습니다. "
                f"영상은 {processed_dir/'videos.csv'}에 저장됐습니다. "
                "--max-units를 올려 댓글만 다시 받으세요."
            )
        for index, (video_id, topics) in enumerate(matched, 1):
            try:
                comment_rows.extend(
                    collect_comments_for_video(
                        client,
                        video_id=video_id,
                        matched_queries=topics,
                        matched_windows=["panel"],
                        collected_at=collected_at,
                        limit=args.comments,
                        include_replies=False,
                        raw_path=raw_dir / "comment_threads.jsonl",
                        order="relevance",
                    )
                )
            except YouTubeAPIError as exc:
                # 댓글 비활성화·삭제된 영상은 정상적인 결과다. 기록하고 넘어간다.
                comment_errors.append(
                    {"video_id": video_id, "status_code": exc.status_code, "reason": exc.reason, "message": exc.message}
                )
                if exc.reason not in {"commentsDisabled", "videoNotFound", "forbidden"}:
                    raise
            if index % 100 == 0:
                print(f"[comments] {index}/{len(matched)}편, 댓글 {len(comment_rows):,}건, {spent_units(client):,}유닛")
        write_csv(processed_dir / "comments.csv", comment_rows)
        if comment_errors:
            write_csv(processed_dir / "comment_errors.csv", comment_errors)
        covered = len(matched) - len(comment_errors)
        print(f"[comments] 댓글 {len(comment_rows):,}건 / 영상 {covered}편 수집, {len(comment_errors)}편 댓글 없음·비활성")

    manifest = {
        "run_id": run_id,
        "collected_at": collected_at,
        "method": "channel_uploads_panel",
        "config": {
            "seed_videos_file": args.seed_videos,
            "seed_video_count": len(seed_video_ids),
            "channels_csv": args.channels_csv,
            "published_after": args.published_after,
            "max_units": args.max_units,
            "comments_per_video": args.comments,
            "comment_order": "relevance" if args.comments else None,
            "include_replies": False,
        },
        "counts": {
            "seed_channels": len(channel_ids),
            "channels_resolved": len(channels),
            "unique_videos": len(rows),
            "topic_matched_videos": len(matched),
            "comment_rows": len(comment_rows),
            "videos_without_comments": len(comment_errors),
        },
        "api_calls": dict(client.calls),
        "quota_units_spent": spent_units(client),
        "limitations": [
            "모집단은 시드 채널 집합이며 전체 YouTube가 아니다(고정 패널).",
            "패널 밖 신규 채널·신규 브랜드의 등장은 관측되지 않는다.",
            "조회수·좋아요는 collected_at 시점 스냅샷이다.",
            "업로드 플레이리스트 최신순 가정에 기반해 cutoff에서 조기 종료한다.",
            "댓글은 주제 사전에 걸린 영상만 받는다. 전체 영상의 댓글 분모는 존재하지 않는다.",
            "댓글 published_at은 댓글 자체 시각이다. 분기 귀속은 video_id로 부모 영상에 붙인다.",
            "댓글은 계속 쌓이므로 최근 분기는 구조적으로 과소 집계된다.",
            "order=relevance는 유튜브 비공개 알고리즘이며 좋아요 순이 아니다.",
        ],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[done] {len(rows)}편 / {spent_units(client)}유닛 소모 / {run_dir}")
    return run_dir


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시드 채널의 업로드를 전수 수집한다.")
    parser.add_argument("--seed-videos", default="seeds/seed_videos.txt", help="시드 영상 URL 목록 파일")
    parser.add_argument("--channels-csv", help="채널 패널 CSV (channel_id, panel_role)")
    parser.add_argument("--channel-id", action="append", help="채널 ID 직접 지정 (반복 가능)")
    parser.add_argument("--comments", type=int, default=0, help="주제 걸린 영상당 받을 댓글 수. 0이면 안 받는다. 1~100은 영상당 1유닛으로 비용이 같다")
    parser.add_argument("--published-after", default="2023-08-01T00:00:00Z", help="이 시점 이후 업로드만")
    parser.add_argument("--output-dir", default="data/panel")
    parser.add_argument("--max-units", type=int, default=2000, help="추정 소모가 이 값을 넘으면 중단")
    return parser.parse_args(argv)


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
