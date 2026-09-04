"""One `analyze polarity` stage — extraction and classification in one pass, filling need_mention and
wish_mention (T14).

There is one entry point, run(conn, ...): the stage wiring in cosmai/cli.py is where #5 ties the three units
together.

To fit the time limits of needs_runtime (statement_timeout 30s · transaction_timeout 60s ·
idle_in_transaction 15s, db/bootstrap.sql) reads are split by keyset paging and writes by batch commits —
analysis/linker/pipeline.py solves the same constraint in the same shape.
Only rows of its own version family (rule-v*) are deleted and refreshed: the seed (slice-*) is neither
deleted nor refreshed (the LIKE filter of NEED_DELETE stops the delete, and the natural key of 005, which
carries extractor_version, stops the insert).
Where two polarity implementations coexist inside one family they are split by (scope, period) — the months
from the `since` of a lexicon_category the ownership table (ownership.py) assigned are written and deleted by
that owner alone, and the months before it are kept up to date by the rules.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import astuple, dataclass
from datetime import date
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.extractor import RuleExtractor
from analysis.lexicon import load_aspects, load_lexicon
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET, RulePolarity, ruleset_for
from analysis.polarity.ownership import OWNERS, Owner, Scopes, may_write, scopes_of
from analysis.types import (
    AspectLexicon,
    Candidate,
    Lexicon,
    NeedMentionRow,
    Polarity,
    PolarityRequest,
    PolarityResult,
    TextUnit,
    WishMentionRow,
)
from analysis.units import CategoryMap, comment_unit, load_category_map, month_of, review_unit

COMMERCE_SCHEMA = "trend_radar"
YOUTUBE_SCHEMA = "tubedepth"
BATCH = 2000  # The same value as #2: a size one transaction finishes inside 60s
FIRST = ""  # The first key of the keyset paging (every source key is text)
FIVE = 5.0

REVIEW_COLUMNS = ("source", "product_key", "review_key", "rating", "body", "written_at", "captured_at")
REVIEW_KEY = ("source", "review_key")  # The PK of trend_radar.review
REVIEW_KEY_AT = (0, 2)  # Their positions inside REVIEW_COLUMNS
REVIEW_REF_AT = (1, 2)  # The positions that make up need_mention.ref (product_key/review_key)
COMMENT_COLUMNS = ("video_id", "comment_id", "text", "like_count", "published_at", "first_seen_at")
COMMENT_KEY = ("video_id", "comment_id")  # The PK of tubedepth.comments
COMMENT_KEY_AT = (0, 1)

RUN_START: LiteralString = "INSERT INTO analysis_run (versions, note) VALUES (%s::jsonb, %s) RETURNING run_id"
RUN_END: LiteralString = (
    "UPDATE analysis_run SET finished_at = now(), status = 'ok', note = %s WHERE run_id = %s"
)
RUN_NOTE: LiteralString = "UPDATE analysis_run SET note = %s WHERE run_id = %s"
# The window in which a month is only half there runs from the DELETE commit of `replace_stale` to the last
# flush of that month. A run that dies inside it leaves the source intact but that month's need_mention in
# halves, and because the rules exclude a scope that has an owner (#31) nobody fills it until a person runs it
# again. That the run is inside the window is said by the DB.
MARKER = "rewriting="
# One predicate carrying may_write from ownership.py into SQL — the delete statement and DO UPDATE use the
# same one. The first line keeps rows of someone else's (scope, period) out (a NULL lexicon_category matches
# no pair and passes), and the second confines a registered implementation to its own (scope, period). With
# both arrays empty this is exactly the behaviour from before ownership.
OWNED: LiteralString = """(
  NOT EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[]) AS theirs(scope, since)
              WHERE theirs.scope = need_mention.lexicon_category AND need_mention.month >= theirs.since)
  AND (cardinality(%s::text[]) = 0
       OR EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[]) AS mine(scope, since)
                  WHERE mine.scope = need_mention.lexicon_category AND need_mention.month >= mine.since))
)"""
NEED_DELETE: LiteralString = (
    """
DELETE FROM need_mention WHERE src = %s AND month = %s AND extractor_version LIKE 'rule-v%%'
AND NOT (extractor_version = %s AND polarity_version = %s)
AND """
    + OWNED
    + "\n"
)
# A --scope run rewrites only that lexicon_category — without narrowing the delete the same way it removes
# rows it will not write again. This was harmless only for rule runs: the moment polarity_version changes the
# whole month becomes stale.
NEED_DELETE_SCOPED: LiteralString = NEED_DELETE + "AND lexicon_category = %s\n"
# `--since D` cut only the reads (`_months` · `_pages`): the delete for the month D falls in stayed whole, so
# every run deleted that month's rows before D and rewrote only those after it — put into cron as it was, it
# digs a hole every day (#98).
# The axis is `observed_at` on the delete side and its value is the source's `coalesce(written_at,
# captured_at)`, so both filters point at the same set of rows (analysis/units.py).
DELETE_SINCE: LiteralString = "AND observed_at >= %s\n"
WISH_DELETE: LiteralString = """
DELETE FROM wish_mention WHERE src = %s AND month = %s AND extractor_version LIKE 'rule-v%%'
AND extractor_version <> %s
"""
# The one line an incremental run asks per page: which of these (src, ref) it has already written *in exactly
# the shape it would write now*. Both versions are in it because that is the shape of the row this run would
# make — if either goes up a new row appears, so "already done" becomes false. It rides the leading columns of
# the natural-key index of 005 (src, ref, need_key, extractor_version, md5(sentence)), so one page's worth
# comes in inside the 30s limit.
MINE_ALREADY: LiteralString = (
    "SELECT DISTINCT ref FROM need_mention WHERE src = %s AND ref = ANY(%s::text[]) "
    "AND extractor_version = %s AND polarity_version = %s"
)
# The rules and any implementation outside the table own nothing, so 'the rows of my version' is the whole
# rule population — the incremental run loses its meaning.
NO_MISSING = (
    "--missing needs an owned (scope, since): {version} owns none, so 'the rows of my version' is the "
    "whole rule population; register it in analysis/polarity/ownership.py or drop --missing"
)
# Since 005 put extractor_version into the natural key, a seed row (slice-*) never collides with this INSERT
# in the first place — a colliding row is always the same version as this run, so DO UPDATE needs no version
# filter.
NEED_UPSERT: LiteralString = (
    """
INSERT INTO need_mention
  (src, site, ref, product_ref, source_product_key, category, lexicon_category, need_key, aspect_scope,
   polarity, strength, rating, observed_at, observed_at_resolution, month, sentence, kind, marker,
   polarity_reason, extractor_version, polarity_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref, need_key, extractor_version, md5(sentence)) DO UPDATE
SET site = EXCLUDED.site, product_ref = EXCLUDED.product_ref,
    source_product_key = EXCLUDED.source_product_key, category = EXCLUDED.category,
    lexicon_category = EXCLUDED.lexicon_category, aspect_scope = EXCLUDED.aspect_scope,
    polarity = EXCLUDED.polarity, strength = EXCLUDED.strength, rating = EXCLUDED.rating,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution,
    month = EXCLUDED.month, kind = EXCLUDED.kind, marker = EXCLUDED.marker,
    polarity_reason = EXCLUDED.polarity_reason, extractor_version = EXCLUDED.extractor_version,
    polarity_version = EXCLUDED.polarity_version
WHERE """
    + OWNED
    + "\n"
)
# The last line is the same predicate as NEED_DELETE — when the stored lexicon_category · month belong to
# someone else it is not refreshed either. When the stored scope and the current mapping diverge (the newest
# rank_snapshot row; category_map recomputes it daily) this run takes that sentence for its own and extracts
# it again: if two implementations pick the same need_key the natural key overlaps entirely and an in-place
# upsert swaps out the owner's row, the one that escaped the delete. That is the place WISH_UPSERT uses
# (DO UPDATE ... WHERE).
WISH_UPSERT: LiteralString = """
INSERT INTO wish_mention
  (src, ref, video_id, channel_id, channel_is_brand_owner, product_ref, observed_at,
   observed_at_resolution, month, wish_class, brand, format, attribute, marker, sentence, like_count,
   extractor_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref) DO UPDATE
SET video_id = EXCLUDED.video_id, channel_id = EXCLUDED.channel_id, product_ref = EXCLUDED.product_ref,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution,
    month = EXCLUDED.month, wish_class = EXCLUDED.wish_class, brand = EXCLUDED.brand,
    format = EXCLUDED.format, attribute = EXCLUDED.attribute, marker = EXCLUDED.marker,
    sentence = EXCLUDED.sentence, like_count = EXCLUDED.like_count,
    extractor_version = EXCLUDED.extractor_version
WHERE wish_mention.extractor_version LIKE 'rule-v%%'
"""


@dataclass(frozen=True)
class StageResult:
    run_id: int
    months: int
    units: int
    need_rows: int
    wish_rows: int
    replaced: int
    captured_at_fallbacks: int  # formats.md: the moment this stops being 0 is when to revisit the time rule
    polarity_version: str = RulePolarity.version
    missing: bool = False

    @property
    def note(self) -> str:
        # An incremental run always reports replaced=0 — on its own that is indistinguishable from 'there was
        # nothing to delete', so the ledger cannot say what made this run's T (#32 measures the T of this
        # command).
        mode = " missing=1" if self.missing else ""
        return (
            f"analyze:polarity:{self.polarity_version}{mode} units={self.units} need={self.need_rows} "
            f"wish={self.wish_rows} replaced={self.replaced} "
            f"captured_at_fallback={self.captured_at_fallbacks}"
        )


@dataclass(frozen=True)
class _Pending:
    """One page's worth of candidates — classification goes once per lexicon after the whole page is in
    (classify_many)."""

    unit: TextUnit
    lexicon_category: str | None
    candidate: Candidate


def _rewriting(base: str, src: str, month: str, scope: str | None) -> str:
    return f"{base} {MARKER}{src}/{month}" + (f"/{scope}" if scope else "")


def _note(conn: psycopg.Connection[Any], run_id: int, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute(RUN_NOTE, (note, run_id))
    conn.commit()


def _table(schema: str, table: str) -> pgsql.Composed:
    return pgsql.SQL("{}.{}").format(pgsql.Identifier(schema), pgsql.Identifier(table))


def _month(observed: str, fallback: str) -> pgsql.Composed:
    return pgsql.SQL("to_char(coalesce({}, {}), 'YYYY-MM')").format(
        pgsql.Identifier(observed), pgsql.Identifier(fallback)
    )


def _exists(conn: psycopg.Connection[Any], schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{table}",))
        row = cur.fetchone()
    conn.rollback()
    return bool(row and row[0])


def _months(
    conn: psycopg.Connection[Any], table: pgsql.Composed, observed: str, fallback: str, since: date | None
) -> list[str]:
    month = _month(observed, fallback)
    where = (
        pgsql.SQL("WHERE coalesce({}, {})::date >= %s").format(
            pgsql.Identifier(observed), pgsql.Identifier(fallback)
        )
        if since
        else pgsql.SQL("")
    )
    query = pgsql.SQL("SELECT DISTINCT {m} FROM {t} {w} ORDER BY 1").format(m=month, t=table, w=where)
    with conn.cursor() as cur:
        cur.execute(query, (since,) if since else ())
        found = [r[0] for r in cur.fetchall() if r[0]]
    conn.rollback()
    return found


def _pages(
    conn: psycopg.Connection[Any],
    table: pgsql.Composed,
    columns: Sequence[str],
    key: Sequence[str],
    key_at: Sequence[int],
    observed: str,
    fallback: str,
    month: str,
    since: date | None,
    batch: int,
) -> Iterator[list[tuple[Any, ...]]]:
    """Reads one month sliced by the PK keyset — there is no guarantee a whole month comes in inside 30s."""
    selected = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in columns)
    ordering = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in key)
    where = pgsql.SQL("({}) > ({}) AND {} = %s").format(
        ordering, pgsql.SQL(", ").join(pgsql.SQL("%s") for _ in key), _month(observed, fallback)
    )
    if since:
        where = pgsql.SQL("{} AND coalesce({}, {})::date >= %s").format(
            where, pgsql.Identifier(observed), pgsql.Identifier(fallback)
        )
    query = pgsql.SQL("SELECT {c} FROM {t} WHERE {w} ORDER BY {o} LIMIT %s").format(
        c=selected, t=table, w=where, o=ordering
    )
    cursor: tuple[Any, ...] = (FIRST,) * len(key)
    while True:
        params = (*cursor, month, since, batch) if since else (*cursor, month, batch)
        with conn.cursor() as cur:
            cur.execute(query, params)
            page = cur.fetchall()
        # Closed as soon as it is read: held open during classification, idle_in_transaction 15s cuts the
        # session.
        conn.rollback()
        if not page:
            return
        yield page
        if len(page) < batch:
            return
        cursor = tuple(page[-1][i] for i in key_at)


def _refs_of(page: Sequence[tuple[Any, ...]], at: Sequence[int]) -> list[str]:
    """need_mention.ref is the two source keys joined with '/' (review_unit in analysis/units.py)."""
    first, second = at
    return [f"{row[first]}/{row[second]}" for row in page]


def _product_facts(
    conn: psycopg.Connection[Any], schema: str
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """A product's newest category and name — a review row has neither (the same material as p1
    extract_candidates)."""
    categories: dict[tuple[str, str], str] = {}
    names: dict[tuple[str, str], str] = {}
    if _exists(conn, schema, "rank_snapshot"):
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT DISTINCT ON (source, product_key) source, product_key, category_name "
                    "FROM {} WHERE category_name IS NOT NULL "
                    "ORDER BY source, product_key, captured_at DESC"
                ).format(_table(schema, "rank_snapshot"))
            )
            categories = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        conn.rollback()
    if _exists(conn, schema, "product"):
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT DISTINCT ON (source, product_key) source, product_key, name FROM {} "
                    "ORDER BY source, product_key, captured_at DESC"
                ).format(_table(schema, "product"))
            )
            names = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        conn.rollback()
    return categories, names


def _channels(conn: psycopg.Connection[Any], schema: str) -> dict[str, tuple[str | None, int | None]]:
    if not _exists(conn, schema, "video_snapshots"):
        return {}
    with conn.cursor() as cur:
        cur.execute(
            pgsql.SQL(
                "SELECT DISTINCT ON (video_id) video_id, channel_id, view_count FROM {} "
                "ORDER BY video_id, fetched_at DESC"
            ).format(_table(schema, "video_snapshots"))
        )
        found = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    conn.rollback()
    return found


class PolarityStage:
    """Builds the lexicons and the rules once, then runs the src x month batches."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        batch: int = BATCH,
        polarity: Polarity | None = None,
        owners: Mapping[str, Owner] = OWNERS,
    ) -> None:
        self.conn = conn
        self.batch = batch
        self.extractor = RuleExtractor()
        # The rule instance stays even when the classifier changes: aspect_scope is a fact the lexicon states,
        # not a result of classification.
        self.rule = RulePolarity()
        self.polarity: Polarity = polarity or self.rule
        self.owners = owners
        # (scope, period) owned by someone else — this run neither writes nor deletes there (ownership.py).
        self.foreign: Scopes = scopes_of(owners, self.polarity.version, mine=False)
        # (scope, period) owned by this run — when it is not empty this run writes and deletes only inside it.
        self.owned: Scopes = scopes_of(owners, self.polarity.version, mine=True)
        self.aspects: dict[str, AspectLexicon] = {
            name: load_aspects(conn, name) for name in (SUNCARE_RULESET, GENERIC_RULESET)
        }
        self.lexicon: Lexicon = load_lexicon(conn)
        self.categories: CategoryMap = load_category_map(conn)
        conn.rollback()

    def versions(self) -> dict[str, Any]:
        return {
            "extractor": RuleExtractor.version,
            "polarity": self.polarity.version,
            "lexicon": {"entity": self.lexicon.version, "aspect": self.aspects[GENERIC_RULESET].version},
        }

    def _owner_args(self) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
        """The five arguments the OWNED predicate takes — two pairs of theirs, three of mine (the cardinality
        check looks at the first array once more)."""
        theirs = [list(part) for part in zip(*self.foreign, strict=True)] or [[], []]
        mine = [list(part) for part in zip(*self.owned, strict=True)] or [[], []]
        return (theirs[0], theirs[1], mine[0], mine[0], mine[1])

    def owns(self, lexicon_category: str | None, month: str) -> bool:
        """What the read skip asks — the same predicate as OWNED in the delete and update statements
        (may_write in ownership.py)."""
        return may_write(self.owners, self.polarity.version, lexicon_category, month)

    def floor(self, scope: str | None) -> str | None:
        """The first month this run may write a single row in — none when it is not an owner (every month is
        the rules').

        An owner can write nothing in the months before its `since` (`owns`), and the delete statement for
        those months is caught by the same predicate and touches 0 rows. Walking those months is therefore the
        pure cost of one DELETE and one paging pass, multiplied daily once the 26 categories of #31 are taken
        out. `ALWAYS` is smaller than any YYYY-MM and cuts nothing.
        """
        reach = [since for owned, since in self.owned if scope is None or owned == scope]
        return min(reach) if reach else None

    def already(self, src: str, refs: Sequence[str]) -> frozenset[str]:
        """Which of these source rows **already** have a row in the shape this run would write now — where the
        incremental run skips.

        A review with no candidate at all gets a row from no run (even the need_key='' sentinel needs a
        candidate), so it never matches here and goes through extraction every time — extraction is rules and
        cheap, and the classifier is not called.
        """
        with self.conn.cursor() as cur:
            cur.execute(MINE_ALREADY, (src, list(refs), RuleExtractor.version, self.polarity.version))
            found = frozenset(row[0] for row in cur.fetchall())
        # Closed as soon as it is read — the same reason as `_pages` (idle_in_transaction 15s).
        self.conn.rollback()
        return found

    def _scope_of(self, aspects: AspectLexicon, category: str | None, aspect: str) -> str:
        for pattern in self.rule.patterns_for(aspects, category):
            if pattern.aspect == aspect:
                return pattern.scope
        return "generic"

    def candidates(self, unit: TextUnit, lexicon_category: str | None) -> list[_Pending]:
        aspects = self.aspects[ruleset_for(lexicon_category)]
        return [
            _Pending(unit, lexicon_category, candidate)
            for candidate in self.extractor.candidates(unit, aspects, lexicon_category)
        ]

    def need_rows(self, pending: Sequence[_Pending]) -> list[NeedMentionRow]:
        """Grouped per lexicon, one classify_many each — for an implementation with a batch API (#6) a round
        trip per sentence is full price."""
        grouped: dict[str, list[int]] = {}
        for i, item in enumerate(pending):
            grouped.setdefault(ruleset_for(item.lexicon_category), []).append(i)
        rows: list[NeedMentionRow | None] = [None] * len(pending)
        for ruleset, indexes in grouped.items():
            aspects = self.aspects[ruleset]
            found = self.polarity.classify_many(
                [
                    PolarityRequest(
                        pending[i].candidate.sentence, pending[i].unit.rating, pending[i].lexicon_category
                    )
                    for i in indexes
                ],
                aspects,
            )
            for i, result in zip(indexes, found, strict=True):
                rows[i] = self._row(pending[i], result, aspects)
        return [row for row in rows if row is not None]

    def _row(self, item: _Pending, found: PolarityResult, aspects: AspectLexicon) -> NeedMentionRow:
        unit = item.unit
        strength = (
            round(1 - unit.rating / FIVE, 2)
            if unit.src == "review" and unit.rating is not None
            else unit.like_count
        )
        return NeedMentionRow(
            src=unit.src,
            site=unit.site,
            ref=unit.ref,
            product_ref=None,  # the linker of #2 fills it in analyze link
            source_product_key=unit.product_key,
            category=unit.category,
            lexicon_category=item.lexicon_category,
            need_key=found.aspect or "",  # B8: no aspect is the '' sentinel
            aspect_scope=self._scope_of(aspects, item.lexicon_category, found.aspect)
            if found.aspect
            else None,
            polarity=found.polarity,
            strength=strength,
            rating=unit.rating,
            observed_at=unit.observed_at,
            observed_at_resolution=unit.observed_at_resolution,
            month=month_of(unit.observed_at),
            sentence=item.candidate.sentence,
            kind=item.candidate.kind,
            marker=item.candidate.marker,
            polarity_reason=found.reason,
            extractor_version=RuleExtractor.version,
            polarity_version=self.polarity.version,
        )

    def wish_row(self, unit: TextUnit) -> WishMentionRow | None:
        found = self.extractor.wishes(unit, self.lexicon)
        if found is None:
            return None
        return WishMentionRow(
            src=unit.src,
            ref=unit.ref,
            video_id=unit.ref.split("/", 1)[0],
            channel_id=unit.channel_id,
            channel_is_brand_owner=None,  # deciding a brand channel needs the linker's brand lexicon (#2)
            product_ref=None,
            observed_at=unit.observed_at,
            observed_at_resolution=unit.observed_at_resolution,
            month=month_of(unit.observed_at),
            wish_class=found.wish_class,
            brand=found.brand,
            format=found.format,
            attribute=found.attribute,
            marker=found.marker,
            sentence=found.sentence,
            like_count=unit.like_count,
            extractor_version=RuleExtractor.version,
        )

    def replace_stale(self, src: str, month: str, scope: str | None = None, since: date | None = None) -> int:
        """Deletes only the old rows of its own version family that this run will write again — a transaction
        of its own.

        `since` narrows what is to be rewritten by exactly that much, so the delete narrows with it (#98):
        without that, the rows of that month before D are deleted and never written again.
        """
        need: LiteralString = NEED_DELETE if scope is None else NEED_DELETE_SCOPED
        wish: LiteralString = WISH_DELETE
        versions = (RuleExtractor.version, self.polarity.version, *self._owner_args())
        args: tuple[Any, ...] = (src, month, *versions) if scope is None else (src, month, *versions, scope)
        wish_args: tuple[Any, ...] = (src, month, RuleExtractor.version)
        if since is not None:
            need, args = need + DELETE_SINCE, (*args, since)
            wish, wish_args = wish + DELETE_SINCE, (*wish_args, since)
        with self.conn.cursor() as cur:
            cur.execute(need, args)
            replaced = cur.rowcount
            # wish_mention has no lexicon_category and a scoped run creates no wish row at all.
            # The same holds for an implementation with an owner — ownership does not hold on that table, so
            # those rows are the rules'.
            if scope is None and not self.owned:
                cur.execute(wish, wish_args)
                replaced += cur.rowcount
        self.conn.commit()
        return replaced

    def _write(self, statement: LiteralString, rows: Sequence[Any], extra: tuple[Any, ...] = ()) -> None:
        # The INSERT column order = the field order of the contract dataclass (interfaces.md) + the predicate
        # arguments of DO UPDATE.
        for start in range(0, len(rows), self.batch):
            with self.conn.cursor() as cur:
                cur.executemany(statement, [astuple(r) + extra for r in rows[start : start + self.batch]])
            self.conn.commit()

    def flush(self, needs: Sequence[NeedMentionRow], wishes: Sequence[WishMentionRow]) -> None:
        self._write(NEED_UPSERT, needs, self._owner_args())
        self._write(WISH_UPSERT, wishes)


def run(
    conn: psycopg.Connection[Any],
    *,
    since: date | None = None,
    scope: str | None = None,
    missing: bool = False,
    commerce_schema: str = COMMERCE_SCHEMA,
    youtube_schema: str = YOUTUBE_SCHEMA,
    batch: int = BATCH,
    polarity: Polarity | None = None,
    owners: Mapping[str, Owner] = OWNERS,
    on_run_open: Callable[[int], None] | None = None,
) -> StageResult:
    """`on_run_open` hands the caller the run_id at the moment the run row is opened — so the caller knows
    *its own* run even when this stage dies inside. Without it the row to close has to be found again in the
    table, and the table holds no clue to tell it from someone else's run going at the same time
    (analysis/pipeline.py)."""
    stage = PolarityStage(conn, batch, polarity, owners)
    version = stage.polarity.version
    # Someone else's scope is refused regardless of since: the months before an owner's period are where the
    # scope-less 05:00 line runs, so naming it with --scope is a lost hand run whatever month it aimed at.
    if scope is not None and any(taken == scope for taken, _ in stage.foreign):
        # A refusal, not a silent no-op — a hand run missing `--impl` has to stop here to see the table.
        raise ValueError(
            f"{scope} is owned by {owners[scope].version} since {owners[scope].since}, not {version} "
            "(analysis/polarity/ownership.py)"
        )
    # Same place and same shape as the refusal of someone else's scope: stopping before the run is opened is
    # what makes the operator look at the table.
    if missing and not stage.owned:
        raise ValueError(NO_MISSING.format(version=version))
    # When a night with broken wiring dies at the first batch, `--missing` puts no rewriting mark on it and
    # there is nowhere to trace that death — an optional hook the rules do not have, so it is found by name
    # (preflight in analysis/polarity/ollama.py).
    if (probe := getattr(stage.polarity, "preflight", None)) is not None:
        probe()
    floor = stage.floor(scope)
    with conn.cursor() as cur:
        cur.execute(
            RUN_START,
            (json.dumps(stage.versions(), ensure_ascii=False), f"analyze:polarity:{version}"),
        )
        row = cur.fetchone()
    run_id = int(row[0]) if row else 0
    conn.commit()
    if on_run_open is not None:
        on_run_open(run_id)
    base_note = f"analyze:polarity:{version}"

    months = units = need_rows = wish_rows = replaced = fallbacks = 0

    if _exists(conn, commerce_schema, "review"):
        categories, names = _product_facts(conn, commerce_schema)
        table = _table(commerce_schema, "review")
        for month in _months(conn, table, "written_at", "captured_at", since):
            if floor is not None and month < floor:
                continue
            months += 1
            # The incremental run only adds what is not there, it swaps nothing out — nothing is deleted, so
            # there is no window leaving the month in halves and no rewriting mark either (with one,
            # `_abandoned` would report a healthy month as stale).
            if not missing:
                _note(conn, run_id, _rewriting(base_note, "review", month, scope))
                replaced += stage.replace_stale("review", month, scope, since)
            for page in _pages(
                conn,
                table,
                REVIEW_COLUMNS,
                REVIEW_KEY,
                REVIEW_KEY_AT,
                "written_at",
                "captured_at",
                month,
                since,
                batch,
            ):
                pending: list[_Pending] = []
                # This, not a date, is what picks them: collection arrives late so written_at cannot pick out
                # "the ones not done", and reclassifying the same sentence changes the label because gemma4 is
                # non-deterministic (#98).
                done = stage.already("review", _refs_of(page, REVIEW_REF_AT)) if missing else frozenset()
                for source, product_key, review_key, rating, body, written_at, captured_at in page:
                    fallbacks += written_at is None
                    unit = review_unit(
                        source=source,
                        product_key=product_key,
                        review_key=review_key,
                        body=body,
                        rating=rating,
                        written_at=written_at,
                        captured_at=captured_at,
                        category=categories.get((source, product_key)),
                    )
                    lexicon_category = stage.categories.lexicon_category(
                        source, unit.category, names.get((source, product_key))
                    )
                    # A (scope, month) that has another owner is not classified at all: the natural key has no
                    # polarity_version, so a single row leaking through here has its upsert overwrite the
                    # owner's label in place.
                    if (
                        not stage.owns(lexicon_category, month_of(unit.observed_at))
                        or (scope and lexicon_category != scope)
                        or unit.ref in done
                    ):
                        continue
                    units += 1
                    pending.extend(stage.candidates(unit, lexicon_category))
                needs = stage.need_rows(pending)
                need_rows += len(needs)
                stage.flush(needs, ())
            if not missing:
                _note(conn, run_id, base_note)

    # A comment has no product category, so a scoped run can create no row here — such a run entering this
    # branch would only delete and leave (the need rows and wish rows of yt_comment vanish for that month).
    # The same holds for an implementation with an owner: ownership does not hold on a row without a
    # lexicon_category, so not one classification gets through.
    if scope is None and not stage.owned and _exists(conn, youtube_schema, "comments"):
        videos = _channels(conn, youtube_schema)
        table = _table(youtube_schema, "comments")
        for month in _months(conn, table, "published_at", "first_seen_at", since):
            months += 1
            _note(conn, run_id, _rewriting(base_note, "yt_comment", month, None))
            replaced += stage.replace_stale("yt_comment", month, since=since)
            for page in _pages(
                conn,
                table,
                COMMENT_COLUMNS,
                COMMENT_KEY,
                COMMENT_KEY_AT,
                "published_at",
                "first_seen_at",
                month,
                since,
                batch,
            ):
                pending = []
                wishes: list[WishMentionRow] = []
                for video_id, comment_id, text, like_count, published_at, first_seen_at in page:
                    fallbacks += published_at is None
                    channel_id, view_count = videos.get(video_id, (None, None))
                    unit = comment_unit(
                        video_id=video_id,
                        comment_id=comment_id,
                        text=text,
                        like_count=like_count,
                        published_at=published_at,
                        first_seen_at=first_seen_at,
                        channel_id=channel_id,
                        view_count=view_count,
                    )
                    # A comment has no product category — only the generic rules run, without a category
                    # lexicon.
                    units += 1
                    pending.extend(stage.candidates(unit, None))
                    wish = stage.wish_row(unit)
                    if wish is not None:
                        wishes.append(wish)
                needs = stage.need_rows(pending)
                need_rows += len(needs)
                wish_rows += len(wishes)
                stage.flush(needs, wishes)
            _note(conn, run_id, base_note)

    result = StageResult(
        run_id=run_id,
        months=months,
        units=units,
        need_rows=need_rows,
        wish_rows=wish_rows,
        replaced=replaced,
        captured_at_fallbacks=fallbacks,
        polarity_version=version,
        missing=missing,
    )
    with conn.cursor() as cur:
        cur.execute(RUN_END, (result.note, run_id))
    conn.commit()
    return result
