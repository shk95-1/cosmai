"""`needs.corpus_*` → `needs.metrics_topic_quarter` (포크 #5).

셈은 SQL 이 하고 수식은 `analysis.trend` 가 한다. 갈라 둔 이유가 골든이다 -- 같은 수식이 코퍼스
표에서도 원 수집 CSV 에서도 돌아야 ydc `trend.py` 출력과 1:1 로 맞댈 수 있다.

읽기 질의는 전부 GROUP BY 로 접혀 돌아온다(가장 큰 것이 주제×분기×채널). 26만 행을 파이썬으로
끌어오지 않는 것은 성능이 아니라 수명 문제다: `needs_runtime` 의 `idle_in_transaction_session_timeout`
은 15초라, 커서를 연 채로 계산하면 연결이 끊긴다. 그래서 읽자마자 `conn.commit()` 하고, 그 뒤로는
DB 를 보지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg

from analysis.retrieval import topics as topic_registry
from analysis.trend import METRIC_VERSION, Counts, VideoPanel, rows
from analysis.types import MetricsTopicQuarterRow
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

# 판정·보고 모집단은 product 하나다 (코퍼스 매니페스트 규칙 5). 어휘는 metrics_need.scope 와 같다.
SCOPE = "선블록"
PANEL_ROLE = "product"
# 모집단 필터는 텍스트 재매칭이 아니라 코퍼스가 이미 단 언급이다 (규칙 6).
TOPIC_FILTER = "선크림"
# 분모는 장문 영상만이다 -- 023 의 어휘(video_long)와 022 의 어휘(long_form)는 다른 표의 것이다.
CORPUS_LONG = "video_long"
CONTENT_TYPE = "long_form"
VIDEO = "youtube_video"
COMMENT = "youtube_comment"
# 댓글을 부모로 되찾는 길은 (snapshot_id, parent_item_id) WHERE content_type='comment' 부분 인덱스다
# (023). 두 술어를 나란히 두는 것은 계약이 그 둘의 동치를 보장하지 않기 때문이고, 부분 인덱스는
# `content_type` 으로 골라지므로 계획은 그대로다 -- `source` 하나만 걸면 26만 행을 훑는다.
CORPUS_COMMENT = "comment"
# 언급량 집계는 quality_flags 가 빈 문서만 세고, 중복 포함 분모는 같은 영상 안 복붙까지 센다 (규칙 9).
COUNTED_FLAGS = ("", "duplicate_in_parent")

# 분기는 저장돼 있지 않다. UTC 로 고정하는 것은 세션 TimeZone 이 분기 경계의 영상을 옆 분기로 옮기기
# 때문이고, 수집기의 analysis_month 도 UTC 다 (13,979편 전수 대조).
QUARTER = "to_char(d.published_at AT TIME ZONE 'UTC', 'YYYY\"Q\"Q')"

POPULATION: LiteralString = f"""
WITH panel AS (
  SELECT channel_id FROM panel_channel
   WHERE version = %(panel_version)s AND panel_role = %(panel_role)s AND active
), video AS (
  SELECT d.doc_id, d.source_item_id, d.channel_id, {QUARTER} AS quarter
    FROM corpus_document d
    JOIN panel p ON p.channel_id = d.channel_id
   WHERE d.snapshot_id = %(snapshot)s AND d.source = '{VIDEO}'
     AND d.content_type = '{CORPUS_LONG}'
     AND EXISTS (SELECT 1 FROM corpus_mention m
                  WHERE m.snapshot_id = d.snapshot_id AND m.doc_id = d.doc_id
                    AND m.topic_id = %(topic_filter)s)
)
"""  # noqa: S608

# 그 분기 그 모집단의 문서 수와, 그 분기에 산출에 든 패널 채널 수.
VIDEO_DOCUMENTS: LiteralString = (
    POPULATION
    + """
SELECT quarter, count(*), count(DISTINCT channel_id) FROM video GROUP BY quarter
"""
)
# 주제×분기×채널 하나가 한 행이다 -- 언급 수·채널 수·엔트로피의 분포가 다 이 한 질의에서 나온다.
VIDEO_MENTIONS: LiteralString = (
    POPULATION
    + """
SELECT m.topic_id, v.quarter, v.channel_id, count(*)
  FROM video v
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = v.doc_id AND m.trend_use
 GROUP BY 1, 2, 3
"""
)
# 댓글의 분기는 자기 시각이 아니라 부모 영상의 분기다 (규칙 3).
COMMENT_DOCUMENTS: LiteralString = (
    POPULATION
    + f"""
SELECT v.quarter, count(*)
  FROM corpus_document c
  JOIN video v ON v.source_item_id = c.parent_item_id
 WHERE c.snapshot_id = %(snapshot)s AND c.content_type = '{CORPUS_COMMENT}'
   AND c.source = '{COMMENT}' AND c.quality_flags = ''
 GROUP BY 1
"""
)  # noqa: S608
# counted=false 행이 unique_ratio 의 분모에만 드는 몫이다 -- 복붙은 반응 1건으로 세지 않는다.
COMMENT_MENTIONS: LiteralString = (
    POPULATION
    + f"""
SELECT m.topic_id, v.quarter, c.quality_flags = '' AS counted,
       count(*), count(DISTINCT c.channel_id)
  FROM corpus_document c
  JOIN video v ON v.source_item_id = c.parent_item_id
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = c.doc_id AND m.trend_use
 WHERE c.snapshot_id = %(snapshot)s AND c.content_type = '{CORPUS_COMMENT}'
   AND c.source = '{COMMENT}' AND c.quality_flags = ANY(%(flags)s)
 GROUP BY 1, 2, 3
"""
)  # noqa: S608
# 축의 두 변은 갈라져 있다 (interfaces.md §분기 표의 행 집합): 분기는 이 산출에 존재하는 것이고 주제는
# 레지스트리(`aspect_lexicon(ruleset='retrieval-topic')`)의 `trend_use=true` 전부다. 관측 distinct 로
# 축을 만들면 한 번도 안 걸린 주제가 표에서 조용히 사라지는데, 격자는 여전히 직사각형이라 불변식 뷰가
# 그것을 잡지 못한다. 이 질의는 그래서 축이 아니라 축 밖의 관측을 찾는 데 쓴다.
OBSERVED_TOPICS: LiteralString = (
    "SELECT DISTINCT topic_id FROM corpus_mention WHERE snapshot_id = %s AND trend_use ORDER BY 1"
)

FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"
REOPEN_RUN: LiteralString = (
    "UPDATE analysis_run SET status = 'running', finished_at = NULL, versions = %s::jsonb WHERE run_id = %s"
)
OPEN_RUN: LiteralString = (
    "INSERT INTO analysis_run (status, versions, note) VALUES ('running', %s::jsonb, %s) RETURNING run_id"
)
CLOSE_RUN: LiteralString = "UPDATE analysis_run SET status = 'ok', finished_at = now() WHERE run_id = %s"
# TODO(#200): `content_type` is in neither this predicate nor note_of(), so a short_form run
# deletes the same run's long_form rows.
CLEAR: LiteralString = (
    "DELETE FROM metrics_topic_quarter "
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
INSERT: LiteralString = """
INSERT INTO metrics_topic_quarter
  (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role,
   mentions, documents, quarter_mentions, denom_channels, composition, velocity_yoy,
   persistence, persist_quarters, window_quarters, unique_ratio, channel_count,
   channel_diffusion, sample_ok)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


# 저장된 행에 대고 두 불변식을 되묻는다 (db/views/metrics_topic_quarter_violation.sql).
VIOLATIONS: LiteralString = (
    "SELECT violation, quarter, detail FROM metrics_topic_quarter_violation WHERE run_id = %s"
)


class TopicAxisDrift(LookupError):
    """스냅샷이 레지스트리 밖의 trend_use 주제를 들고 있다. 그 언급은 어느 행에도 quarter_mentions
    에도 들지 못해 분모에서 조용히 빠지므로, 사전 버전이 갈린 채로 표를 세우지 않는다."""


class NoPopulation(LookupError):
    """분모가 설 자리가 없다. 0 을 조용히 내는 대신 멈춘다 -- 비율이 없는 것과 0 은 다른 말이다."""


@dataclass(frozen=True)
class QuarterOutcome:
    run_id: int
    snapshot_id: int
    panel_version: int
    written: int
    quarters: int
    topics: int
    counts: dict[str, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not self.violations else "partial"

    @property
    def note(self) -> str:
        by_source = " ".join(f"{name}={n}" for name, n in sorted(self.counts.items()))
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend quarter run={self.run_id} snapshot={self.snapshot_id} "
            f"panel=v{self.panel_version} topics={self.topics} quarters={self.quarters} "
            f"rows={self.written} {by_source}{tail}"
        ).strip()


def note_of(scope: str, snapshot_id: int, panel_version: int) -> str:
    """persistence 는 run 상대라, 스냅샷이나 명부가 바뀌면 같은 분기가 다른 값을 갖는 새 run 이어야 한다."""
    return f"trend-quarter:{METRIC_VERSION}:{scope}:snapshot{snapshot_id}:panel{panel_version}"


def topic_axis(conn: psycopg.Connection[Any], cur: psycopg.Cursor[Any], snapshot_id: int) -> list[str]:
    """레지스트리의 `trend_use=true` 주제 전부. 순서는 사전 적재 순서이고 ydc `trend.py` 도 그렇다."""
    axis = [entry["topic"] for entry in topic_registry.load(conn).entries if entry["trend_use"]]
    cur.execute(OBSERVED_TOPICS, (snapshot_id,))
    unknown = [topic for (topic,) in cur.fetchall() if topic not in set(axis)]
    if unknown:
        raise TopicAxisDrift(
            f"snapshot {snapshot_id} mentions {unknown} with trend_use, but the active "
            f"{topic_registry.RULESET} dictionary does not carry them -- {topic_registry.FIX}"
        )
    return axis


def _run_id(cur: psycopg.Cursor[Any], note: str) -> int:
    """note 로 찾고 없을 때만 만든다 -- 재실행이 run 을 쌓으면 멱등이 관측되지 않는다."""
    payload = json.dumps({"metric": METRIC_VERSION}, ensure_ascii=False)
    cur.execute(FIND_RUN, (note,))
    found = cur.fetchone()
    if found:
        cur.execute(REOPEN_RUN, (payload, found[0]))
        return int(found[0])
    cur.execute(OPEN_RUN, (payload, note))
    created = cur.fetchone()
    assert created is not None
    return int(created[0])


def _video_counts(cur: psycopg.Cursor[Any], params: Mapping[str, Any]) -> tuple[Counts, VideoPanel]:
    cur.execute(VIDEO_DOCUMENTS, dict(params))
    documents: dict[str, int] = {}
    denom_channels: dict[str, int] = {}
    for quarter, docs, channels in cur.fetchall():
        documents[quarter] = int(docs)
        denom_channels[quarter] = int(channels)
    cur.execute(VIDEO_MENTIONS, dict(params))
    mentions: dict[tuple[str, str], int] = {}
    per_channel: dict[tuple[str, str], dict[str, int]] = {}
    for topic, quarter, channel, count in cur.fetchall():
        key = (topic, quarter)
        mentions[key] = mentions.get(key, 0) + int(count)
        per_channel.setdefault(key, {})[channel] = int(count)
    channels = {key: len(dist) for key, dist in per_channel.items()}
    # 영상은 한 문서가 한 번만 세어지므로 중복 포함 언급 수가 언급 수와 같다 -- unique_ratio 는 1 이다.
    return Counts(documents, mentions, dict(mentions), channels), VideoPanel(denom_channels, per_channel)


def _comment_counts(cur: psycopg.Cursor[Any], params: Mapping[str, Any]) -> Counts:
    cur.execute(COMMENT_DOCUMENTS, dict(params))
    documents = {quarter: int(docs) for quarter, docs in cur.fetchall()}
    cur.execute(COMMENT_MENTIONS, {**params, "flags": list(COUNTED_FLAGS)})
    mentions: dict[tuple[str, str], int] = {}
    raw: dict[tuple[str, str], int] = {}
    channels: dict[tuple[str, str], int] = {}
    for topic, quarter, counted, count, distinct in cur.fetchall():
        key = (topic, quarter)
        raw[key] = raw.get(key, 0) + int(count)
        if counted:
            mentions[key] = int(count)
            channels[key] = int(distinct)
    return Counts(documents, mentions, raw, channels)


@dataclass(frozen=True)
class Built:
    """적재 전의 산출 한 벌. 골든과 테스트는 DB 에 쓰지 않고 이것만 본다."""

    run_id: int
    snapshot_id: int
    panel_version: int
    rows: list[MetricsTopicQuarterRow]
    counts: dict[str, int]


def build(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> Built:
    """읽고, 트랜잭션을 닫고, 수식을 돌린다. 그 순서가 15초 타임아웃을 피하는 유일한 모양이다."""
    # TODO(#201): run() opens and commits the run first, so if the population is empty and run()
    # aborts, status='running' is left behind.
    with conn.cursor() as cur:
        # 활성 판본을 고르는 길은 하나다 -- 맨 `WHERE active` 는 판본이 둘일 때 분모를 두 배로 만든다.
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None:
            raise NoPopulation("no active panel roster; run `python -m db.seed --only panel` first")
        if snapshot is None:
            raise NoPopulation("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        params = {
            "snapshot": snapshot,
            "panel_version": version,
            "panel_role": panel_role,
            "topic_filter": TOPIC_FILTER,
        }
        topics = topic_axis(conn, cur, snapshot)
        video, video_panel = _video_counts(cur, params)
        comment = _comment_counts(cur, params)
        run_id = _run_id(cur, note_of(scope, snapshot, version))
    conn.commit()

    built: list[MetricsTopicQuarterRow] = []
    counts: dict[str, int] = {}
    for source, source_counts in ((VIDEO, video), (COMMENT, comment)):
        made = rows(
            topics,
            source_counts,
            video_panel,
            run_id=run_id,
            scope=scope,
            source=source,
            content_type=CONTENT_TYPE,
            panel_version=version,
            panel_role=panel_role,
        )
        counts[source] = len(made)
        built.extend(made)
    return Built(run_id, snapshot, version, built, counts)


def _values(row: MetricsTopicQuarterRow) -> tuple[Any, ...]:
    return (
        row.run_id, row.scope, row.topic_key, row.quarter, row.source, row.content_type,
        row.panel_version, row.panel_role, row.mentions, row.documents, row.quarter_mentions,
        row.denom_channels, row.composition, row.velocity_yoy, row.persistence,
        row.persist_quarters, row.window_quarters, row.unique_ratio, row.channel_count,
        row.channel_diffusion, row.sample_ok,
    )  # fmt: skip


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> QuarterOutcome:
    """한 스냅샷의 분기 표를 통째로 다시 쓴다. 부분 갱신이 아닌 것이 격자를 조밀하게 지킨다."""
    made = build(
        conn, scope=scope, panel_role=panel_role, snapshot_id=snapshot_id, panel_version=panel_version
    )
    if not made.rows:
        raise NoPopulation(
            f"the active snapshot has no {CORPUS_LONG} document in the {panel_role} panel that "
            f"mentions {TOPIC_FILTER!r}; nothing to write"
        )
    with conn.cursor() as cur:
        # 재실행이 옛 행을 남기면 격자가 조밀하지 않게 되고, 그것을 뷰가 sparse_grid 로 잡는다.
        cur.execute(CLEAR, (made.run_id, scope, made.panel_version, panel_role))
        cur.executemany(INSERT, [_values(row) for row in made.rows])
        cur.execute(CLOSE_RUN, (made.run_id,))
        # 계약 문장이 아니라 저장된 행이 답한다 -- 격자가 조밀한가, 분모가 닫히는가.
        violations = [
            f"{name} {quarter or '-'} {detail}" for name, quarter, detail in _asked(cur, made.run_id)
        ]
    conn.commit()
    return QuarterOutcome(
        run_id=made.run_id,
        snapshot_id=made.snapshot_id,
        panel_version=made.panel_version,
        written=len(made.rows),
        quarters=len({row.quarter for row in made.rows}),
        topics=len({row.topic_key for row in made.rows}),
        counts=made.counts,
        violations=violations,
    )


def _asked(cur: psycopg.Cursor[Any], run_id: int) -> list[tuple[str, str | None, str]]:
    cur.execute(VIOLATIONS, (run_id,))
    return [(str(a), b, str(c)) for a, b, c in cur.fetchall()]
