"""`needs.corpus_*` → `needs.metrics_topic_quarter` (fork #5).

SQL does the counting and `analysis.trend` does the formulas. The reason they are apart is the golden set --
the same formulas have to run on the corpus tables and on the raw collection CSV for the output to line up
1:1 with ydc `trend.py`.

Every read query comes back folded by GROUP BY (the largest is topic x quarter x channel). Not pulling 260k
rows into Python is a matter of lifetime rather than performance: `needs_runtime`'s
`idle_in_transaction_session_timeout` is 15 seconds, so computing with a cursor open cuts the connection. So
`conn.commit()` happens as soon as the read is done, and after that the DB is not looked at.
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

# The population for judging and reporting is product alone (corpus manifest rule 5). The vocabulary is the
# same as metrics_need.scope.
SCOPE = "선블록"
PANEL_ROLE = "product"
# The population filter is the mention the corpus already attached, not a rematch over the text (rule 6).
TOPIC_FILTER = "선크림"
# The denominator is long videos only -- the vocabulary of 023 (video_long) and that of 022 (long_form)
# belong to different tables.
CORPUS_LONG = "video_long"
CONTENT_TYPE = "long_form"
VIDEO = "youtube_video"
COMMENT = "youtube_comment"
# The way back from a comment to its parent is the partial index (snapshot_id, parent_item_id) WHERE
# content_type='comment' (023). The two predicates sit side by side because the contract does not guarantee
# they are equivalent, and the partial index is chosen by `content_type` so the plan is unchanged -- with
# `source` alone it scans 260k rows.
CORPUS_COMMENT = "comment"
# The mention count counts only documents whose quality_flags is empty; the duplicate-inclusive denominator
# counts copy-paste inside the same video as well (rule 9).
COUNTED_FLAGS = ("", "duplicate_in_parent")

# The quarter is not stored. It is pinned to UTC because the session TimeZone moves a video on a quarter
# boundary into the next quarter, and the collector's analysis_month is UTC too (compared over all 13,979).
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

# The documents of that population in that quarter, and the panel channels that entered the output then.
VIDEO_DOCUMENTS: LiteralString = (
    POPULATION
    + """
SELECT quarter, count(*), count(DISTINCT channel_id) FROM video GROUP BY quarter
"""
)
# One topic x quarter x channel is one row -- the distribution of mentions, channels and entropy all comes
# out of this one query.
VIDEO_MENTIONS: LiteralString = (
    POPULATION
    + """
SELECT m.topic_id, v.quarter, v.channel_id, count(*)
  FROM video v
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = v.doc_id AND m.trend_use
 GROUP BY 1, 2, 3
"""
)
# The quarter of a comment is the quarter of its parent video, not its own timestamp (rule 3).
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
# The share of counted=false rows is the denominator of unique_ratio alone -- copy-paste is not counted as
# one reaction.
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
# The two sides of the axis are split (interfaces.md §The quarterly table's row set): the quarters are those
# existing in this output, and the topics are
# every `trend_use=true` row of the registry (`aspect_lexicon(ruleset='retrieval-topic')`). Building the axis
# from observed distinct values would make a topic that never hit disappear from the table quietly, while the
# grid stays rectangular and the invariant view does not catch it. So this query is used to find observations
# outside the axis rather than to build the axis.
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


# The two invariants are asked back against the stored rows (db/views/metrics_topic_quarter_violation.sql).
VIOLATIONS: LiteralString = (
    "SELECT violation, quarter, detail FROM metrics_topic_quarter_violation WHERE run_id = %s"
)


class TopicAxisDrift(LookupError):
    """The snapshot holds a trend_use topic outside the registry. Those mentions can enter neither a row nor
    quarter_mentions and so drop quietly out of the denominator, so the table is not built while the
    dictionary version is out of step."""


class NoPopulation(LookupError):
    """There is nowhere for the denominator to stand. It stops rather than emit 0 quietly -- having no ratio
    and having 0 are different statements."""


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
    """persistence is relative to the run, so a changed snapshot or roster has to be a new run for the same
    quarter to take a different value."""
    return f"trend-quarter:{METRIC_VERSION}:{scope}:snapshot{snapshot_id}:panel{panel_version}"


def topic_axis(conn: psycopg.Connection[Any], cur: psycopg.Cursor[Any], snapshot_id: int) -> list[str]:
    """Every `trend_use=true` topic of the registry. The order is the dictionary load order, as in ydc
    `trend.py`."""
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
    """Found by note and created only when there is none -- piling up runs on a rerun makes idempotence
    unobservable."""
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
    # A video counts one document once, so the duplicate-inclusive mention count equals the mention count --
    # unique_ratio is 1.
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
    """One output set before loading. The golden set and the tests write nothing to the DB and look only at
    this."""

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
    """Read, close the transaction, run the formulas. That order is the only shape that avoids the 15-second
    timeout."""
    # TODO(#201): run() opens and commits the run first, so if the population is empty and run()
    # aborts, status='running' is left behind.
    with conn.cursor() as cur:
        # There is one way to pick the active revision -- a bare `WHERE active` doubles the denominator when
        # there are two revisions.
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
    """Rewrites the quarter table of one snapshot wholesale. Not being a partial update is what keeps the
    grid dense."""
    made = build(
        conn, scope=scope, panel_role=panel_role, snapshot_id=snapshot_id, panel_version=panel_version
    )
    if not made.rows:
        raise NoPopulation(
            f"the active snapshot has no {CORPUS_LONG} document in the {panel_role} panel that "
            f"mentions {TOPIC_FILTER!r}; nothing to write"
        )
    with conn.cursor() as cur:
        # A rerun leaving old rows makes the grid non-dense, and the view catches that as sparse_grid.
        cur.execute(CLEAR, (made.run_id, scope, made.panel_version, panel_role))
        cur.executemany(INSERT, [_values(row) for row in made.rows])
        cur.execute(CLOSE_RUN, (made.run_id,))
        # The stored rows answer, not a sentence of the contract -- is the grid dense, does the denominator
        # close.
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
