"""`needs.metrics_topic_quarter` → `needs.topic_quarter_judgement` (포크 #40).

판정은 지표를 다시 세지 않는다. ydc 가 `trend.py` 와 `judge.py` 를 갈라 둔 이유가 그것이고 -- 판정
기준(tau·가중치·유형 이름)은 팀 합의로 바뀌지만 그때 지표는 그대로다 -- 이 파이프라인은 그 분리를
저장에서도 지킨다: 입력은 문서가 아니라 **그 run 이 이미 저장한 행**이고, 산출은 그 행과 1:1 이다.

읽기는 한 run 의 지표 행 전부(전량 338행)라 파이썬으로 끌어와도 작다. 그래도 읽자마자
`conn.commit()` 하는 것은 `needs_runtime` 의 `idle_in_transaction_session_timeout` 이 15초여서다
(`analysis/trend/pipeline.py` 와 같은 자리).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg

from analysis.judge import JUDGEMENT_VERSION, UNJUDGED, judge
from analysis.trend.pipeline import PANEL_ROLE, SCOPE, note_of
from analysis.types import MetricsTopicQuarterRow, TopicQuarterJudgementRow
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

METRIC_COLUMNS = (
    "run_id", "scope", "topic_key", "quarter", "source", "content_type", "panel_version",
    "panel_role", "mentions", "documents", "quarter_mentions", "denom_channels", "composition",
    "velocity_yoy", "persistence", "persist_quarters", "window_quarters", "unique_ratio",
    "channel_count", "channel_diffusion", "sample_ok",
)  # fmt: skip
SELECT_METRICS: LiteralString = (
    f"SELECT {', '.join(METRIC_COLUMNS)} FROM metrics_topic_quarter "  # noqa: S608
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"
# 판정 행은 지표 행과 같은 run 에 산다(FK 가 run_id 를 포함한다). 그래서 판본도 그 run 의 versions 에
# 키 하나로 얹힌다 -- 지표를 다시 세지 않고 기준만 바꾸면 움직이는 것은 이 키다 (versioning.md).
STAMP_VERSION: LiteralString = (
    "UPDATE analysis_run SET versions = coalesce(versions, '{}'::jsonb) || %s::jsonb WHERE run_id = %s"
)
# TODO(#200): same four-column predicate as trend/evidence, missing content_type here too.
CLEAR: LiteralString = (
    "DELETE FROM topic_quarter_judgement "
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
INSERT: LiteralString = """
INSERT INTO topic_quarter_judgement
  (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role,
   trend_type, judged, evidence_strength, single_source, opportunity_score, gap_pp, hold_reason)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
VIOLATIONS: LiteralString = (
    "SELECT violation, quarter, detail FROM topic_quarter_judgement_violation WHERE run_id = %s"
)


class NoJudgement(LookupError):
    """판정할 지표 행이 없다. 아직 안 세운 것이라 실패가 아니라 막힘이다 -- 0행을 조용히 쓰면
    `unjudged_cell` 불변식이 참인 채로 표가 비어 버린다."""


@dataclass(frozen=True)
class Built:
    """적재 전의 판정 한 벌. 골든과 테스트는 DB 에 쓰지 않고 이것만 본다."""

    run_id: int
    snapshot_id: int
    panel_version: int
    rows: list[TopicQuarterJudgementRow]


@dataclass(frozen=True)
class JudgeOutcome:
    run_id: int
    snapshot_id: int
    panel_version: int
    written: int
    by_type: dict[str, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not self.violations else "partial"

    @property
    def judged(self) -> int:
        return sum(n for kind, n in self.by_type.items() if kind not in UNJUDGED)

    @property
    def note(self) -> str:
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend judge run={self.run_id} snapshot={self.snapshot_id} "
            f"panel=v{self.panel_version} rows={self.written} judged={self.judged}{tail}"
        )


def _metric_rows(
    cur: psycopg.Cursor[Any], run_id: int, scope: str, version: int, role: str
) -> list[MetricsTopicQuarterRow]:
    """numeric 은 Decimal 로 온다 -- 계약의 dataclass 는 float 이고, 판정 수식도 float 위에서 돈다."""
    cur.execute(SELECT_METRICS, (run_id, scope, version, role))
    made: list[MetricsTopicQuarterRow] = []
    for row in cur.fetchall():
        fields = dict(zip(METRIC_COLUMNS, row, strict=True))
        for name in ("composition", "velocity_yoy", "persistence", "unique_ratio", "channel_diffusion"):
            fields[name] = None if fields[name] is None else float(fields[name])
        made.append(MetricsTopicQuarterRow(**fields))
    return made


def build(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Built:
    """그 run 의 지표 행을 읽고, 트랜잭션을 닫고, 판정한다. run 을 찾는 길은 `trend quarter` 와 같다."""
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None:
            raise NoJudgement("no active panel roster; run `python -m db.seed --only panel` first")
        if snapshot is None:
            raise NoJudgement("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        cur.execute(FIND_RUN, (note_of(scope, snapshot, version),))
        found = cur.fetchone()
        if found is None:
            raise NoJudgement(
                f"no quarter run for {scope!r} on snapshot {snapshot}; run `cosmai trend quarter`"
            )
        run_id = int(found[0])
        metrics = _metric_rows(cur, run_id, scope, version, panel_role)
    conn.commit()

    if not metrics:
        raise NoJudgement(f"run {run_id} has no metrics_topic_quarter row to judge")
    return Built(run_id, snapshot, version, judge(metrics))


def _values(row: TopicQuarterJudgementRow) -> tuple[Any, ...]:
    return (
        row.run_id, row.scope, row.topic_key, row.quarter, row.source, row.content_type,
        row.panel_version, row.panel_role, row.trend_type, row.judged, row.evidence_strength,
        row.single_source, row.opportunity_score, row.gap_pp, row.hold_reason,
    )  # fmt: skip


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> JudgeOutcome:
    """그 (run, scope, 명부) 의 판정 행을 통째로 다시 쓴다 -- 부분 갱신이면 1:1 이 깨진다."""
    made = build(
        conn, scope=scope, panel_role=panel_role, snapshot_id=snapshot_id, panel_version=panel_version
    )
    payload = json.dumps({"judgement": JUDGEMENT_VERSION}, ensure_ascii=False)
    by_type: dict[str, int] = {}
    for row in made.rows:
        by_type[row.trend_type] = by_type.get(row.trend_type, 0) + 1
    with conn.cursor() as cur:
        cur.execute(CLEAR, (made.run_id, scope, made.panel_version, panel_role))
        cur.executemany(INSERT, [_values(row) for row in made.rows])
        cur.execute(STAMP_VERSION, (payload, made.run_id))
        # 계약 문장이 아니라 저장된 행이 답한다 -- 지표 행과 1:1 인가, gap_pp 가 두 행에서 같은가.
        cur.execute(VIOLATIONS, (made.run_id,))
        violations = [f"{name} {quarter or '-'} {detail}" for name, quarter, detail in cur.fetchall()]
    conn.commit()
    return JudgeOutcome(
        run_id=made.run_id,
        snapshot_id=made.snapshot_id,
        panel_version=made.panel_version,
        written=len(made.rows),
        by_type=by_type,
        violations=violations,
    )
