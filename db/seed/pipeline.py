"""The declaration of pipeline stages -- the values of `needs.pipeline_stage` (#138).

If the expected interval only lives in `stack/crontab.d/`, the portal cannot read it (PostgREST only
sees the DB). So instead of parsing the crontab, this **declares** it here, and a test catches any
drift from the crontab (`tests/test_pipeline_stage.py`).

The reason the crontab is not treated as the source of truth is `enabled`: `youtube watch` **has** a
cron line but does not run because it sits behind a compose profile (`stack/docker-compose.yml`, the
condition for turning it back on is #39). Neither the crontab nor the DB knows that fact, so someone
has to declare it -- automation only removes half of the problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, LiteralString, NamedTuple

import psycopg

from db.seed._common import counts, write


class Stage(NamedTuple):
    stage_key: str
    arm: str
    dataset: str
    expected_interval: str
    enabled: bool
    note: str


# stage_key is `<arm>:<dataset>`. analyze's two rows can share a subcommand, so only the incremental
# pass gets `_missing` appended -- the same vocabulary as analysis_run.note splitting on `missing=`.
STAGES: tuple[Stage, ...] = (
    Stage("commerce:ranking", "commerce", "ranking", "1 hour", True, "매시 랭킹 스냅샷"),
    Stage("commerce:product", "commerce", "product", "1 day", True, "상세 페이지 렌더 — 보드 5개 × 상위 17"),
    Stage("commerce:review_low", "commerce", "review_low", "1 day", True, "선케어 저평점 리뷰 깊게"),
    Stage("commerce:review", "commerce", "review", "1 day", True, "리뷰 본문"),
    Stage(
        "commerce:review_stats", "commerce", "review_stats", "1 day", True, "리뷰 수·별점 분포 = 모집단 분모"
    ),
    Stage("commerce:new_product", "commerce", "new_product", "1 day", True, "신제품 등재"),
    Stage("youtube:work", "youtube", "work", "5 min", True, "일감 큐 소비"),
    Stage("youtube:flatten", "youtube", "flatten", "15 min", True, "수집분 평탄화"),
    Stage("youtube:prune", "youtube", "prune", "1 day", True, "오래된 산출물 정리"),
    # A cron line exists but the container never starts because it sits behind a compose profile
    # (STATE.md §2). The condition for turning it back on is #39.
    Stage("youtube:watch", "youtube", "watch", "1 hour", False, "profiles: youtube-watch 뒤 — 재가동은 #39"),
    # The table has zero rows. It is enabled because the cron has a line, and freshness sits at never
    # (#138 user decision).
    # The values are filled in by fork cosmai-import-ydc#53 (DataLab 128-month collection).
    Stage("naver:datalab", "naver", "datalab", "1 mon", True, "검색어 트렌드 — 아직 0행"),
    Stage("naver:blog", "naver", "blog", "1 mon", True, "블로그 — 아직 0행"),
    Stage("analyze:all", "analyze", "all", "1 day", True, "규칙 전량 패스 05:00 UTC"),
    # analyze:polarity_missing (the gemma4 incremental pass) is gone rather than disabled: the crontab
    # carries no line for it at all now, unlike youtube:watch's profile gate above, which still has a
    # cron line to compare against. Suspended 2026-09-06 (#242) -- add it back in the same PR that
    # restores the `0 8` line in stack/crontab.d/analyze.
)


TABLES = ("pipeline_stage", "pipeline_edge")

# This is an upsert because the declaration is the source of truth -- if the interval changes, that
# value has to reach the DB, and an old row must not be left standing.
UPSERT: LiteralString = """
INSERT INTO pipeline_stage (stage_key, arm, dataset, expected_interval, enabled, note)
VALUES (%s, %s, %s, %s::interval, %s, %s)
ON CONFLICT (stage_key) DO UPDATE
SET arm = EXCLUDED.arm, dataset = EXCLUDED.dataset,
    expected_interval = EXCLUDED.expected_interval, enabled = EXCLUDED.enabled, note = EXCLUDED.note
"""


# ---- Edges (#141) --------------------------------------------------------------------------
#
# The bar for picking a storage node: **a table some other stage or screen consumes**. That is what
# "a load-bearing output table" means, and that bar forces a minimal set -- every stage needs at least
# one edge (a test asks for it), so any stage's sole output is necessarily a node.
#
# Left out on purpose, and why:
#   trend_radar.price_point         ranking derives and writes this through RankRecord.to_price().
#                                    Analysis's price axis (price_event/rank_daily) is not on any
#                                    screen yet, so it has no consumer.
#   trend_radar.review_summary/_topic/_answer  A side axis of review. No screen reads it today.
#   tubedepth.video_snapshots/transcripts/listing_entries  Other output of flatten. What analysis
#                                    reads is comments (the reasoning comment next to it in
#                                    db/grants/needs_runtime_reader.sql).
#   run/fetch_log/analysis_run/llm_usage  An execution ledger, not data. That is pipeline_health's job.
#   needs.corpus_*                  Made by the fork's DDL 023 -- an upstream checkout has no such
#                                    table. If the upstream contract referenced someone else's object
#                                    it would be the same spot as #107/#150. analyze in production
#                                    really does read it, so the picture is missing that much, but that
#                                    edge is the fork's share to add to its own contract.
# Bringing back something left out is just adding one more row -- start minimal, as far as the picture
# still reads.

STAGE, STORE = "stage", "store"


class Edge(NamedTuple):
    from_key: str
    from_kind: str
    to_key: str
    to_kind: str
    note: str


def _writes(stage: str, store: str, note: str = "") -> Edge:
    return Edge(stage, STAGE, store, STORE, note)


def _reads(store: str, stage: str, note: str = "") -> Edge:
    return Edge(store, STORE, stage, STAGE, note)


EDGES: tuple[Edge, ...] = (
    # -- commerce writes these. Every dataset starts from the ranking page, but only ranking actually
    #    writes a RankRecord -- the others only use that list as a seed and go straight to follow
    #    (the continue after the wants_* branch in collectors/commerce/sources/oliveyoung.py).
    _writes("commerce:ranking", "trend_radar.rank_snapshot", "RankRecord"),
    _writes("commerce:ranking", "trend_radar.product", "RankRecord.records() 가 함께 낸다"),
    _writes("commerce:product", "trend_radar.product", "상세 페이지 렌더"),
    _writes("commerce:review", "trend_radar.review", "ReviewRecord"),
    _writes("commerce:review_low", "trend_radar.review", "같은 레코드, 저평점 끝을 걷는다"),
    _writes("commerce:review_low", "trend_radar.review_stats", "같은 걸음이 stats 도 따라간다"),
    _writes("commerce:review_stats", "trend_radar.review_stats", "ReviewStatsRecord = 모집단 분모"),
    _writes("commerce:new_product", "trend_radar.new_product", "daisomall"),
    # -- youtube runs three in sequence (the header of collectors/youtube/cli.py).
    _writes("youtube:watch", "tubedepth.jobs", "일감을 넣는다 -- 지금 꺼져 있어 큐가 마른다(#39·#153)"),
    _writes("youtube:work", "tubedepth.artifacts", "job 을 집어 원문을 적재한다"),
    _writes("youtube:flatten", "tubedepth.comments", "artifact 를 질의 가능한 표로 편다"),
    # -- naver
    _writes("naver:datalab", "needs.naver_datalab_point", "검색어 트렌드 -- 아직 0행"),
    _writes("naver:blog", "needs.naver_blog_post", "아직 0행"),
    # -- analysis
    _writes("analyze:all", "needs.need_mention", "추출"),
    _writes("analyze:all", "needs.wish_mention", "추출"),
    _writes("analyze:all", "needs.metrics_need", "집계 -- 화면 1·3·4·5"),
    _writes("analyze:all", "needs.metrics_wish", "집계 -- 화면 2"),
    # analyze:polarity_missing wrote needs.need_mention here -- gone with the stage (#242).
    # -- Reads. The reasoning on the source side is the per-line comment in
    # db/grants/needs_runtime_reader.sql.
    _reads("tubedepth.jobs", "youtube:work", "큐에서 집는다"),
    _reads("tubedepth.jobs", "youtube:prune", "끝난 job 을 늙힌다"),
    _reads("tubedepth.artifacts", "youtube:flatten", ""),
    _reads("tubedepth.artifacts", "youtube:prune", "원문을 늙힌다"),
    _reads("trend_radar.rank_snapshot", "analyze:all", ""),
    _reads("trend_radar.product", "analyze:all", ""),
    _reads("trend_radar.review", "analyze:all", "니즈 언급의 주 원천"),
    _reads("trend_radar.review_stats", "analyze:all", "모집단 분모"),
    _reads("trend_radar.new_product", "analyze:all", ""),
    _reads("tubedepth.comments", "analyze:all", "유튜브 언급의 원천"),
    _reads("needs.need_mention", "analyze:all", "집계가 자기 추출분을 다시 읽는다"),
    # analyze:polarity_missing read needs.need_mention here -- gone with the stage (#242).
    _reads("needs.wish_mention", "analyze:all", ""),
)

EDGE_UPSERT: LiteralString = """
INSERT INTO pipeline_edge (from_key, from_kind, to_key, to_kind, note)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (from_key, to_key) DO UPDATE
SET from_kind = EXCLUDED.from_kind, to_kind = EXCLUDED.to_kind, note = EXCLUDED.note
"""


def load(cur: psycopg.Cursor[Any], _source: Path) -> dict[str, int]:
    """Loads the declared stages. Neither the slice nor eval/ is read, so source is unused."""
    write(cur, UPSERT, [tuple(s) for s in STAGES])
    write(cur, EDGE_UPSERT, [tuple(e) for e in EDGES])
    return counts(cur, TABLES)
