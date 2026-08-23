"""`cosmai analyze <stage>` 의 배선 한 자리: link → polarity → aggregate 를 한 run 으로 묶는다 (#5).

세 유닛의 `pipeline.run()` 은 여기서만 이어 붙는다. 각 단계의 배치 커밋 경계는 그대로 둔다 — 전체를
한 트랜잭션으로 감싸면 needs_runtime 의 transaction_timeout 60s 안에 끝날 수 없다 (db/bootstrap.sql).
run 행은 polarity 가 열고 aggregate 가 그 run_id 로 metrics 를 쓴다: 셋이 한 run 을 나눠 쓴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, LiteralString

import psycopg

from analysis.aggregate import RuleAggregator
from analysis.aggregate import pipeline as aggregate_stage
from analysis.extractor import VERSION as EXTRACTOR_VERSION
from analysis.lexicon import load_aspects, load_lexicon
from analysis.linker import LINKER_VERSION
from analysis.linker import pipeline as link_stage
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET
from analysis.polarity import VERSION as POLARITY_VERSION
from analysis.polarity import pipeline as polarity_stage

__all__ = ["POPULATION", "StageOutcome", "run_stage"]

COMMERCE_SCHEMA = "trend_radar"
YOUTUBE_SCHEMA = "tubedepth"
# aggregate 는 이 run 이 방금 쓴 버전 계열만 센다 — 시드(slice-*)를 같은 scope 에 섞으면 한 문장이 두 번
# 세어지고 어떤 규칙이 만든 수인지 되짚을 수 없다. 고른 모집단은 analysis_run.versions.extractor 에 남는다.
POPULATION = (EXTRACTOR_VERSION,)
RULESETS = (SUNCARE_RULESET, GENERIC_RULESET)
LINK_COUNTS = ("product_ref", "brand_mention")
# 재현에 필요한 것은 첫 줄이다 — psycopg 는 여기에 쿼리 전문을 붙여 note 를 통째로 삼킨다.
DETAIL_CHARS = 160

FAILURES = (psycopg.Error, LookupError, ValueError)
NOTE: LiteralString = "analyze:{stage}"
RUN_FAILED: LiteralString = (
    "INSERT INTO analysis_run (status, finished_at, versions, note) VALUES ('failed', now(), %s::jsonb, %s)"
)
RUN_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = %s, finished_at = now(), versions = %s::jsonb, note = %s "
    "WHERE run_id = %s"
)
METRICS: LiteralString = (
    "SELECT (SELECT count(*) FROM metrics_need WHERE run_id = %s), "
    "(SELECT count(*) FROM metrics_wish WHERE run_id = %s)"
)


@dataclass(frozen=True)
class StageOutcome:
    stage: str
    status: str  # done | failed
    run_id: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    detail: str = ""

    @property
    def note(self) -> str:
        rows = " ".join(f"{name}={n}" for name, n in self.counts.items())
        tail = f" failed:{self.detail}" if self.status != "done" else ""
        return f"{NOTE.format(stage=self.stage)} {rows}{tail}".strip()


def _detail(stage: str, failure: Exception) -> str:
    message = str(failure).strip().splitlines()[0] if str(failure).strip() else type(failure).__name__
    return f"{stage} {message[:DETAIL_CHARS]}"


def _versions(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    """#17 판정: lexicon 은 활성 버전 + ruleset 이다 — aspect 사전은 ruleset 마다 따로 켜진다."""
    lexicon = load_lexicon(conn)
    aspects = {ruleset: load_aspects(conn, ruleset).version for ruleset in RULESETS}
    conn.rollback()
    return {
        "linker": LINKER_VERSION,
        "extractor": EXTRACTOR_VERSION,
        "polarity": POLARITY_VERSION,
        "aggregate": RuleAggregator().version,
        "lexicon": {"entity": lexicon.version, "aspect": aspects},
    }


def _metrics_counts(conn: psycopg.Connection[Any], run_id: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(METRICS, (run_id, run_id))
        row = cur.fetchone()
    conn.rollback()
    return {"metrics_need": int(row[0]), "metrics_wish": int(row[1])} if row else {}


def _close(conn: psycopg.Connection[Any], outcome: StageOutcome, versions: dict[str, Any]) -> StageOutcome:
    payload = json.dumps(versions, ensure_ascii=False)
    with conn.cursor() as cur:
        if outcome.run_id is None:
            cur.execute(RUN_FAILED, (payload, outcome.note))
        else:
            cur.execute(RUN_CLOSE, (outcome.status, payload, outcome.note, outcome.run_id))
    conn.commit()
    return outcome


def run_all(
    conn: psycopg.Connection[Any],
    since: date | None,
    scope: str | None,
    commerce_schema: str,
    youtube_schema: str,
    captured_at: date | None,
) -> StageOutcome:
    counts: dict[str, int] = {}
    versions: dict[str, Any] = {}
    run_id: int | None = None
    stage = "link"
    try:
        versions = _versions(conn)
        linked = link_stage.run(
            conn, since=since, commerce_schema=commerce_schema, youtube_schema=youtube_schema
        )
        counts.update({name: linked[name] for name in LINK_COUNTS})
        stage = "polarity"
        polarity = polarity_stage.run(
            conn,
            since=since,
            scope=scope,
            commerce_schema=commerce_schema,
            youtube_schema=youtube_schema,
        )
        run_id = polarity.run_id
        counts.update({"need_mention": polarity.need_rows, "wish_mention": polarity.wish_rows})
        stage = "aggregate"
        aggregate_stage.run(
            conn,
            scope=scope,
            run_id=run_id,
            commerce_schema=commerce_schema,
            captured_at=captured_at,
            extractors=POPULATION,
        )
        counts.update(_metrics_counts(conn, run_id))
    except FAILURES as failure:
        conn.rollback()
        return _close(conn, StageOutcome("all", "failed", run_id, counts, _detail(stage, failure)), versions)
    return _close(conn, StageOutcome("all", "done", run_id, counts), versions)


def run_stage(
    conn: psycopg.Connection[Any],
    stage: str,
    *,
    since: date | None = None,
    scope: str | None = None,
    commerce_schema: str = COMMERCE_SCHEMA,
    youtube_schema: str = YOUTUBE_SCHEMA,
    captured_at: date | None = None,
) -> StageOutcome:
    if stage == "all":
        return run_all(conn, since, scope, commerce_schema, youtube_schema, captured_at)
    try:
        if stage == "link":
            linked = link_stage.run(
                conn, since=since, commerce_schema=commerce_schema, youtube_schema=youtube_schema
            )
            return StageOutcome(stage, "done", None, {n: linked[n] for n in LINK_COUNTS})
        if stage == "polarity":
            # 이 단계는 자기 run 을 열고 닫는다 — 단독 실행의 run 은 polarity 것이다.
            polarity = polarity_stage.run(
                conn,
                since=since,
                scope=scope,
                commerce_schema=commerce_schema,
                youtube_schema=youtube_schema,
            )
            counts = {"need_mention": polarity.need_rows, "wish_mention": polarity.wish_rows}
            return StageOutcome(stage, "done", polarity.run_id, counts)
        run_id = aggregate_stage.run(
            conn,
            scope=scope,
            commerce_schema=commerce_schema,
            captured_at=captured_at,
            extractors=POPULATION,
        )
        return StageOutcome(stage, "done", run_id, _metrics_counts(conn, run_id))
    except FAILURES as failure:
        conn.rollback()
        return StageOutcome(stage, "failed", None, {}, _detail(stage, failure))
