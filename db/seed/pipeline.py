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


TABLES = ("pipeline_stage",)

# 선언이 정본이므로 갱신이다 -- 주기가 바뀌면 그 값이 DB 에 반영돼야지 옛 행이 남으면 안 된다.
UPSERT: LiteralString = """
INSERT INTO pipeline_stage (stage_key, arm, dataset, expected_interval, enabled, note)
VALUES (%s, %s, %s, %s::interval, %s, %s)
ON CONFLICT (stage_key) DO UPDATE
SET arm = EXCLUDED.arm, dataset = EXCLUDED.dataset,
    expected_interval = EXCLUDED.expected_interval, enabled = EXCLUDED.enabled, note = EXCLUDED.note
"""


def load(cur: psycopg.Cursor[Any], _source: Path) -> dict[str, int]:
    """선언된 단계를 넣는다. 슬라이스도 eval/ 도 읽지 않아 source 를 쓰지 않는다."""
    write(cur, UPSERT, [tuple(s) for s in STAGES])
    return counts(cur, TABLES)
