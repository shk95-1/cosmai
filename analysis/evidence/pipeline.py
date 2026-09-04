"""`needs.corpus_*` + `needs.topic_quarter_judgement` → `needs.topic_quarter_evidence` (포크 #6).

근거는 판정과 달리 **코퍼스를 훑는 단계**다. 그래서 #40 이 만나지 않은 함정을 그대로 만난다:
`needs_runtime` 의 `idle_in_transaction_session_timeout` 이 15초라, 후보를 커서로 들고 접기 시작하면
연결이 끊긴다. 읽자마자 `conn.commit()` 하고 그 뒤로는 DB 를 보지 않는 것이 이 파일의 모양이고,
`analysis/trend/pipeline.py` 가 같은 이유로 같은 모양이다. 끌어오는 것은 본문이 아니라 포인터와
좋아요뿐이다 -- 본문은 뷰가 필요할 때 잇는다. 전량(261,317문서)에서 후보 15,602행 · 질의 178ms ·
명령 전체 0.52s · 최대 상주 73MB 로 **재 봤다**(2026-08-26, 계약 §근거 "전량 실측"); 재지 않은 채
"가볍다"고 적어 두면 그것은 다음 사람이 밟을 단언이다.

**모집단을 다시 적지 않는다.** 지표를 세운 `POPULATION` CTE 를 그대로 import 해서 쓴다. 근거만 다른
모집단에서 고르면 카드가 인용하는 발화와 카드에 적힌 숫자가 다른 분모 위에 서고, 둘 다 그럴듯해서
보이지 않는다 (계약 §근거).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg

from analysis.evidence import EVIDENCE_VERSION, TOP_PER_CELL, Candidate, select
from analysis.trend.pipeline import (
    COMMENT,
    CONTENT_TYPE,
    CORPUS_COMMENT,
    PANEL_ROLE,
    POPULATION,
    SCOPE,
    TOPIC_FILTER,
    note_of,
)
from analysis.types import TopicQuarterEvidenceRow
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"
# 근거가 붙을 자리. 판정된 셀만 읽는 것이 025 의 FK 가 거절할 행을 만들지 않는 방법이다.
CELLS: LiteralString = (
    "SELECT DISTINCT topic_key, quarter, source FROM topic_quarter_judgement "
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
# 두 술어를 나란히 두는 것은 계약이 그 둘의 동치를 보장하지 않기 때문이고, 023 의 부분 인덱스가
# `content_type` 으로 골라지므로 계획은 그대로다 -- `source` 하나만 걸면 26만 행을 훑는다
# (`analysis/trend/pipeline.py` 의 같은 자리, #5 실측).
#
# quality_flags 를 여기서 거르지 않는 것은 게이트가 두 곳에 있으면 갈리기 때문이다. 규칙 넷은
# `analysis/evidence` 하나가 지고, 이 질의는 후보를 데려오기만 한다.
CANDIDATES: LiteralString = (
    POPULATION
    + f"""
SELECT c.doc_id, v.quarter, m.topic_id, c.source, c.channel_id,
       c.source_metadata->>'like_count'          AS like_count,
       c.source_metadata->>'author_channel_hash' AS author_channel_hash,
       c.quality_flags, m.matched_term
  FROM corpus_document c
  JOIN video v ON v.source_item_id = c.parent_item_id
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = c.doc_id AND m.trend_use
 WHERE c.snapshot_id = %(snapshot)s AND c.content_type = '{CORPUS_COMMENT}' AND c.source = '{COMMENT}'
"""
)  # noqa: S608
STAMP_VERSION: LiteralString = (
    "UPDATE analysis_run SET versions = coalesce(versions, '{}'::jsonb) || %s::jsonb WHERE run_id = %s"
)
# TODO(#200): `content_type` is in neither this predicate nor note_of(), so a short_form run
# deletes the same run's long_form evidence -- the same four columns as
# `analysis/trend/pipeline.py`·`analysis/judge/pipeline.py`.
CLEAR: LiteralString = (
    "DELETE FROM topic_quarter_evidence "
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
INSERT: LiteralString = """
INSERT INTO topic_quarter_evidence
  (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role,
   rank, snapshot_id, doc_id, like_count, matched_term)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
VIOLATIONS: LiteralString = (
    "SELECT violation, quarter, detail FROM topic_quarter_evidence_violation WHERE run_id = %s"
)


class NoEvidence(LookupError):
    """근거로 삼을 것이 없다. 아직 안 세운 것이라 실패가 아니라 막힘이다 -- 0행을 조용히 쓰면 빈 표
    위에서도 불변식이 참이 되고, 카드는 "규칙에 걸린 셀이 없다"와 "근거가 없다"를 구분하지 못한다."""


def _int(value: Any) -> int:
    """좋아요는 jsonb 안에서 문자열로 산다. 못 읽는 값은 0 이다 -- ydc 도 그렇게 읽는다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Built:
    """적재 전의 근거 한 벌. 골든과 테스트는 DB 에 쓰지 않고 이것만 본다."""

    run_id: int
    snapshot_id: int
    panel_version: int
    rows: list[TopicQuarterEvidenceRow]
    candidates: list[Candidate]


@dataclass(frozen=True)
class EvidenceOutcome:
    run_id: int
    snapshot_id: int
    panel_version: int
    written: int
    candidates: int
    cells: int
    violations: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not self.violations else "partial"

    @property
    def note(self) -> str:
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend evidence run={self.run_id} snapshot={self.snapshot_id} "
            f"panel=v{self.panel_version} candidates={self.candidates} cells={self.cells} "
            f"rows={self.written}{tail}"
        )


def build(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
    top: int = TOP_PER_CELL,
) -> Built:
    """판정 셀과 후보를 읽고, 트랜잭션을 닫고, 고른다. run 을 찾는 길은 `quarter`·`judge` 와 같다."""
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None:
            raise NoEvidence("no active panel roster; run `python -m db.seed --only panel` first")
        if snapshot is None:
            raise NoEvidence("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        cur.execute(FIND_RUN, (note_of(scope, snapshot, version),))
        found = cur.fetchone()
        if found is None:
            raise NoEvidence(
                f"no quarter run for {scope!r} on snapshot {snapshot}; run `cosmai trend quarter`"
            )
        run_id = int(found[0])
        cur.execute(CELLS, (run_id, scope, version, panel_role))
        cells = {(str(topic), str(quarter), str(source)) for topic, quarter, source in cur.fetchall()}
        if not cells:
            raise NoEvidence(f"run {run_id} has no topic_quarter_judgement row; run `cosmai trend judge`")
        cur.execute(
            CANDIDATES,
            {
                "snapshot": snapshot,
                "panel_version": version,
                "panel_role": panel_role,
                "topic_filter": TOPIC_FILTER,
            },
        )
        candidates = [
            Candidate(
                doc_id=str(doc_id),
                quarter=str(quarter),
                topic_key=str(topic_id),
                source=str(source),
                channel_id=str(channel_id),
                like_count=_int(like_count),
                author_channel_hash=str(author_hash or ""),
                quality_flags=str(flags or ""),
                matched_term=term,
            )
            for doc_id, quarter, topic_id, source, channel_id, like_count, author_hash, flags, term in (
                cur.fetchall()
            )
        ]
    conn.commit()

    if not candidates:
        raise NoEvidence(f"run {run_id} has no comment in the judged population to quote")
    rows = select(
        candidates,
        run_id=run_id,
        scope=scope,
        content_type=CONTENT_TYPE,
        panel_version=version,
        panel_role=panel_role,
        snapshot_id=snapshot,
        cells=cells,
        top=top,
    )
    if not rows:
        raise NoEvidence(
            f"run {run_id} has {len(candidates)} candidates but none survived the gates "
            "(creator's own comment, quality_flags, or a topic outside the judged grid)"
        )
    return Built(run_id, snapshot, version, rows, candidates)


def _values(row: TopicQuarterEvidenceRow) -> tuple[Any, ...]:
    return (
        row.run_id, row.scope, row.topic_key, row.quarter, row.source, row.content_type,
        row.panel_version, row.panel_role, row.rank, row.snapshot_id, row.doc_id,
        row.like_count, row.matched_term,
    )  # fmt: skip


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
    top: int = TOP_PER_CELL,
) -> EvidenceOutcome:
    """그 (run, scope, 명부) 의 근거 행을 통째로 다시 쓴다 -- 부분 갱신이면 자리의 사다리가 구멍 난다."""
    made = build(
        conn,
        scope=scope,
        panel_role=panel_role,
        snapshot_id=snapshot_id,
        panel_version=panel_version,
        top=top,
    )
    payload = json.dumps({"evidence": EVIDENCE_VERSION}, ensure_ascii=False)
    with conn.cursor() as cur:
        cur.execute(CLEAR, (made.run_id, scope, made.panel_version, panel_role))
        cur.executemany(INSERT, [_values(row) for row in made.rows])
        cur.execute(STAMP_VERSION, (payload, made.run_id))
        # 계약 문장이 아니라 저장된 행이 답한다 -- 자리가 1 부터 이어지는가, 근거가 그 셀의 것인가.
        cur.execute(VIOLATIONS, (made.run_id,))
        violations = [f"{name} {quarter or '-'} {detail}" for name, quarter, detail in cur.fetchall()]
    conn.commit()
    return EvidenceOutcome(
        run_id=made.run_id,
        snapshot_id=made.snapshot_id,
        panel_version=made.panel_version,
        written=len(made.rows),
        candidates=len(made.candidates),
        cells=len({(row.topic_key, row.quarter, row.source) for row in made.rows}),
        violations=violations,
    )
