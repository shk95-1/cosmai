"""파이프라인 단계의 선언 — `needs.pipeline_stage` 의 값 (#138).

기대 주기가 `stack/crontab.d/` 에만 있으면 포털이 못 읽는다(PostgREST 는 DB 만 본다). 그래서
크론탭을 파싱하는 대신 여기서 **선언**하고, 크론탭과의 어긋남은 테스트가 막는다
(`tests/test_pipeline_stage.py`).

크론탭을 정본으로 삼지 않은 이유는 `enabled` 다: `youtube watch` 는 크론 줄이 **있는데** compose
profile 뒤라 안 돈다(`stack/docker-compose.yml`, 재가동 조건은 #39). 크론탭도 DB 도 그 사실을
모르므로 누군가는 선언해야 한다 — 자동화는 문제의 절반만 없앤다.
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


# stage_key 는 `<arm>:<dataset>` 이다. analyze 의 두 줄은 하위명령이 같을 수 있어 증분 패스만
# `_missing` 을 붙인다 -- analysis_run.note 가 `missing=` 으로 갈리는 것과 같은 어휘다.
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
    # 크론 줄은 있지만 compose profile 뒤라 컨테이너가 안 뜬다(STATE.md §2). 재가동 조건은 #39.
    Stage("youtube:watch", "youtube", "watch", "1 hour", False, "profiles: youtube-watch 뒤 — 재가동은 #39"),
    # 표가 0행이다. 크론에 줄이 있으니 enabled 이고, freshness 는 never 로 선다(#138 사용자 결정).
    # 값은 포크 cosmai-import-ydc#53(DataLab 128개월 수집)이 채운다.
    Stage("naver:datalab", "naver", "datalab", "1 mon", True, "검색어 트렌드 — 아직 0행"),
    Stage("naver:blog", "naver", "blog", "1 mon", True, "블로그 — 아직 0행"),
    Stage("analyze:all", "analyze", "all", "1 day", True, "규칙 전량 패스 05:00 UTC"),
    Stage(
        "analyze:polarity_missing",
        "analyze",
        "polarity_missing",
        "1 day",
        True,
        "gemma4 증분 패스 08:00 UTC (#32)",
    ),
)


TABLES = ("pipeline_stage", "pipeline_edge")

# 선언이 정본이므로 갱신이다 -- 주기가 바뀌면 그 값이 DB 에 반영돼야지 옛 행이 남으면 안 된다.
UPSERT: LiteralString = """
INSERT INTO pipeline_stage (stage_key, arm, dataset, expected_interval, enabled, note)
VALUES (%s, %s, %s, %s::interval, %s, %s)
ON CONFLICT (stage_key) DO UPDATE
SET arm = EXCLUDED.arm, dataset = EXCLUDED.dataset,
    expected_interval = EXCLUDED.expected_interval, enabled = EXCLUDED.enabled, note = EXCLUDED.note
"""


# ---- 엣지 (#141) --------------------------------------------------------------------------
#
# 저장소 노드를 고르는 기준: **다른 단계나 화면이 소비하는 표**. 그것이 "주요 산출물표" 의 뜻이고,
# 그 기준이 최소 집합을 강제한다 -- 단계마다 적어도 하나의 엣지가 있어야 하므로(테스트가 묻는다)
# 어느 단계의 유일한 산출물은 반드시 노드가 된다.
#
# 일부러 뺀 것과 그 이유:
#   trend_radar.price_point         ranking 이 RankRecord.to_price() 로 파생해 쓴다. 분석의 가격 축
#                                   (price_event·rank_daily)이 아직 화면에 없어 소비자가 없다.
#   trend_radar.review_summary/_topic/_answer  리뷰의 곁가지 축. 지금 어느 화면도 안 읽는다.
#   tubedepth.video_snapshots/transcripts/listing_entries  flatten 의 다른 산출. 분석이 읽는 것은
#                                   comments 다(db/grants/needs_runtime_reader.sql 의 근거 주석).
#   run/fetch_log/analysis_run/llm_usage  실행 원장이지 데이터가 아니다. 그것은 pipeline_health 가 진다.
#   needs.corpus_*                  포크 DDL 023 이 만든다 -- upstream 체크아웃에는 그 표가 없다.
#                                   남의 객체를 upstream 계약이 참조하면 #107·#150 과 같은 자리가
#                                   된다. 운영에서 analyze 가 실제로 그것을 읽으므로 그림이 그만큼
#                                   비지만, 그 엣지는 포크가 제 계약에 더할 몫이다.
# 뺀 것을 되살리는 것은 행 하나 더하는 일이다 -- 그림이 읽히는 한도에서 최소로 시작한다.

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
    # -- commerce 가 쓴다. 모든 dataset 이 랭킹 페이지에서 출발하지만, RankRecord 를 실제로
    #    적는 것은 ranking 뿐이다 -- 나머지는 그 목록을 씨앗으로만 쓰고 곧장 follow 로 간다
    #    (collectors/commerce/sources/oliveyoung.py 의 wants_* 분기 뒤 continue).
    _writes("commerce:ranking", "trend_radar.rank_snapshot", "RankRecord"),
    _writes("commerce:ranking", "trend_radar.product", "RankRecord.records() 가 함께 낸다"),
    _writes("commerce:product", "trend_radar.product", "상세 페이지 렌더"),
    _writes("commerce:review", "trend_radar.review", "ReviewRecord"),
    _writes("commerce:review_low", "trend_radar.review", "같은 레코드, 저평점 끝을 걷는다"),
    _writes("commerce:review_low", "trend_radar.review_stats", "같은 걸음이 stats 도 따라간다"),
    _writes("commerce:review_stats", "trend_radar.review_stats", "ReviewStatsRecord = 모집단 분모"),
    _writes("commerce:new_product", "trend_radar.new_product", "daisomall"),
    # -- youtube 는 셋이 이어 달린다 (collectors/youtube/cli.py 머리말).
    _writes("youtube:watch", "tubedepth.jobs", "일감을 넣는다 -- 지금 꺼져 있어 큐가 마른다(#39·#153)"),
    _writes("youtube:work", "tubedepth.artifacts", "job 을 집어 원문을 적재한다"),
    _writes("youtube:flatten", "tubedepth.comments", "artifact 를 질의 가능한 표로 편다"),
    # -- naver
    _writes("naver:datalab", "needs.naver_datalab_point", "검색어 트렌드 -- 아직 0행"),
    _writes("naver:blog", "needs.naver_blog_post", "아직 0행"),
    # -- 분석
    _writes("analyze:all", "needs.need_mention", "추출"),
    _writes("analyze:all", "needs.wish_mention", "추출"),
    _writes("analyze:all", "needs.metrics_need", "집계 -- 화면 1·3·4·5"),
    _writes("analyze:all", "needs.metrics_wish", "집계 -- 화면 2"),
    _writes("analyze:polarity_missing", "needs.need_mention", "극성만 채운다(증분)"),
    # -- 읽는다. 원천 쪽 근거는 db/grants/needs_runtime_reader.sql 의 줄별 주석이다.
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
    _reads("needs.need_mention", "analyze:polarity_missing", "극성이 빈 행을 고른다"),
    _reads("needs.wish_mention", "analyze:all", ""),
)

EDGE_UPSERT: LiteralString = """
INSERT INTO pipeline_edge (from_key, from_kind, to_key, to_kind, note)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (from_key, to_key) DO UPDATE
SET from_kind = EXCLUDED.from_kind, to_kind = EXCLUDED.to_kind, note = EXCLUDED.note
"""


def load(cur: psycopg.Cursor[Any], _source: Path) -> dict[str, int]:
    """선언된 단계를 넣는다. 슬라이스도 eval/ 도 읽지 않아 source 를 쓰지 않는다."""
    write(cur, UPSERT, [tuple(s) for s in STAGES])
    write(cur, EDGE_UPSERT, [tuple(e) for e in EDGES])
    return counts(cur, TABLES)
