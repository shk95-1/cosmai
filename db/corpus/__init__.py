"""Imports the youtube corpus snapshot ydc handed over into `needs.corpus_*` (fork #4).

The source is the three CSVs in `~/github_prj/Main/archive/yt-handoff/` (261,317, 105,358, 43 rows).
`archive/` may not be modified (STATE.md §3) and its 174M is not copied into the repo either, so this
loader **takes the path as an argument** -- the one reason it does not read a fixed spot inside the repo
the way `db/seed` does.

Three things shaped this file.

1. **Never overwritten.** A re-collection (#38) fetches the same video again under the same unique key
   (`source + source_item_id`), but the view count, likes and comments from 2026-08-19 do not
   reproduce. So the observed version sits at the front of the key (`corpus_document`'s PK), and a
   re-collection arrives under a different `snapshot_id` and stands beside the old row.
2. **Batching and paging.** `needs_runtime` sits under a statement_timeout of 30s and a
   transaction_timeout of 60s (`db/bootstrap.sql`). Putting 260k rows into one transaction hits that
   wall, so this commits page by page -- `analysis/retrieval/corpus.py` uses the same shape for reading,
   for the same reason. So this `load()`, unlike `db/seed/*`, takes a **connection** rather than a
   cursor: whoever commits has to own the transaction.
3. **Idempotent on re-run.** Every INSERT is `ON CONFLICT DO NOTHING`, so running it twice never
   rewrites a value (`imported_at` also stays at its first-load value).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.corpus import contract
from db.seed import panel
from db.seed._common import as_timestamp, counts, opt

TABLES = ("corpus_snapshot", "corpus_document", "corpus_mention")

SNAPSHOT_ID = 1
SNAPSHOT_LABEL = "yt-handoff-20260819"
SNAPSHOT_NOTE = "ydc 인계 코퍼스. 원본 archive/yt-handoff/ (읽기 전용)"

# The row count of one page. Chosen to target an executemany that finishes inside 30 seconds -- go too
# high and statement_timeout loses, go too low and the round-trip count loses.
BATCH = 1000

# One line of the 174M CSV carries a whole video description -- csv's default limit (128KiB) dies with
# _csv.Error on that.
csv.field_size_limit(10**7)

Progress = Callable[[str, int], None]

SNAPSHOT_SQL: LiteralString = """
INSERT INTO corpus_snapshot (snapshot_id, label, produced_by, source_runs, collected_at, note)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id) DO NOTHING
"""
# A re-load never rewrites a row that already has the same value -- rowcount reads as "0 changed".
ACTIVATE_SQL: LiteralString = """
UPDATE corpus_snapshot SET active = (snapshot_id = %s)
WHERE active IS DISTINCT FROM (snapshot_id = %s)
"""
SNAPSHOT_COUNT_SQL: LiteralString = "SELECT count(*) FROM corpus_document WHERE snapshot_id = %s"
ACTIVE_SQL: LiteralString = "SELECT snapshot_id FROM corpus_snapshot WHERE active"

# doc_id is a generated column, so it is not here (023).
DOCUMENT_SQL: LiteralString = """
INSERT INTO corpus_document
  (snapshot_id, source, source_item_id, content_type, parent_item_id, channel_id,
   published_at, url, text, quality_flags, source_metadata, collected_at, source_run)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
ON CONFLICT (snapshot_id, source, source_item_id) DO NOTHING
"""
MENTION_SQL: LiteralString = """
INSERT INTO corpus_mention
  (snapshot_id, doc_id, topic_id, topic_type, trend_use, matched_term, span_start)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id, doc_id, topic_id) DO NOTHING
"""
PANEL_SQL: LiteralString = "SELECT channel_id, panel_role FROM panel_channel WHERE version = %s AND active"


class CorpusMismatch(ValueError):
    """A row to import disagrees with a fact the contract already carries. This stops rather than bend
    to make the numbers fit."""


def read_manifest(source_dir: Path) -> dict[str, Any]:
    """Reads the manifest and asks whether its rules match the sentences the contract carries
    (`db/corpus/contract.py`)."""
    manifest: dict[str, Any] = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    contract.check(manifest)
    return manifest


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    """Streaming. Loading 260k rows as one list of dicts would put the raw 174M text into memory twice.
    `utf-8-sig`: these CSVs carry a BOM, so opening them as utf-8 would make the first column name
    `\\ufeffdoc_id`."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {k: (v or "") for k, v in row.items()}


def runs_by_collected_at(manifest: dict[str, Any]) -> dict[str, str]:
    """Collection time -> run_id. A document row carries no run id, only
    `source_metadata.collected_at`, but the two runs' times differ, so this one table alone recovers
    which run each row came from."""
    return {run["collected_at"]: run["run_id"] for run in manifest["source_run_manifests"]}


def document_row(row: dict[str, str], snapshot_id: int, runs: dict[str, str]) -> tuple[Any, ...]:
    raw = row["source_metadata"] or "{}"
    metadata: dict[str, Any] = json.loads(raw)
    collected_at = metadata.get("collected_at")
    if collected_at not in runs:
        raise CorpusMismatch(
            f"{row['doc_id']}: collected_at {collected_at!r} belongs to no run in the manifest"
        )
    # This asks rule 1's second sentence again, right here: if the CSV's doc_id disagrees with the
    # generated column, the mention join quietly comes up empty.
    expected = f"{row['source']}:{row['source_item_id']}"
    if row["doc_id"] != expected:
        raise CorpusMismatch(f"doc_id {row['doc_id']!r} is not {expected!r} (manifest rule 1)")
    return (
        snapshot_id,
        row["source"],
        row["source_item_id"],
        row["content_type"],
        opt(row["parent_item_id"]),
        row["channel_id"],
        as_timestamp(row["published_at"]),
        opt(row["url"]),
        row["text"],
        row["quality_flags"],
        raw,
        as_timestamp(collected_at),
        runs[collected_at],
    )


def mention_row(row: dict[str, str], snapshot_id: int) -> tuple[Any, ...]:
    return (
        snapshot_id,
        row["doc_id"],
        row["topic_id"],
        row["topic_type"],
        row["trend_use"].lower() == "true",
        opt(row["matched_term"]),
        int(row["span_start"]) if row["span_start"] else None,
    )


def check_channels(cur: psycopg.Cursor[Any], source_dir: Path, panel_version: int) -> int:
    """코퍼스가 언급하는 채널이 전부 활성 명부에 같은 역할로 있는가.

    This function is why `channel.csv` is not made a table. A channel's role is the value that fixes a
    denominator (`contracts/formats.md` §Panel roster CSV), and if it lives in two tables there are two
    denominators, and the later one quietly parts from the earlier. So the roster stays as `panel_channel`
    alone, and the import **refuses** a disagreement.

    돌려주는 것은 명부 크기가 아니라 **읽은 `channel.csv` 의 행수**다 -- 매니페스트의 `table_counts`
    가 세는 것이 그쪽이고, 명부에는 이 코퍼스에 없는 채널도 있을 수 있다.
    """
    cur.execute(PANEL_SQL, (panel_version,))
    roster = {channel_id: role for channel_id, role in cur.fetchall()}
    rows = list(read_csv(source_dir / "channel.csv"))
    problems = [
        f"{row['channel_id']}: corpus says {row['panel_role']}, roster says {roster.get(row['channel_id'])}"
        for row in rows
        if roster.get(row["channel_id"]) != row["panel_role"]
    ]
    if problems:
        raise CorpusMismatch(
            f"channel.csv disagrees with the active panel roster (version {panel_version}): "
            + "; ".join(problems)
        )
    return len(rows)


def _tally(
    rows: Iterator[dict[str, str]],
    counts: dict[str, int],
    name: str,
    by_type: Counter[str] | None = None,
) -> Iterator[dict[str, str]]:
    """Counts a streaming CSV while passing it through -- reading the 174M again just to count it would
    double the import's cost."""
    for row in rows:
        counts[name] = counts.get(name, 0) + 1
        if by_type is not None:
            by_type[row["content_type"]] += 1
        yield row


def _pages(rows: Iterator[tuple[Any, ...]], batch: int) -> Iterator[list[tuple[Any, ...]]]:
    page: list[tuple[Any, ...]] = []
    for row in rows:
        page.append(row)
        if len(page) >= batch:
            yield page
            page = []
    if page:
        yield page


def copy_pages(
    conn: psycopg.Connection[Any],
    statement: LiteralString,
    rows: Iterator[tuple[Any, ...]],
    *,
    batch: int,
    label: str,
    progress: Progress | None,
) -> int:
    """One transaction per page. The return value is the row count actually inserted, so a re-run
    yields 0."""
    inserted = 0
    seen = 0
    for page in _pages(rows, batch):
        with conn.cursor() as cur:
            cur.executemany(statement, page)
            inserted += max(cur.rowcount, 0)
        conn.commit()
        seen += len(page)
        if progress:
            progress(label, seen)
    return inserted


def insert_snapshot(
    cur: psycopg.Cursor[Any], manifest: dict[str, Any], snapshot_id: int, label: str, note: str
) -> None:
    runs = runs_by_collected_at(manifest)
    cur.execute(
        SNAPSHOT_SQL,
        (
            snapshot_id,
            label,
            manifest.get("produced_by"),
            sorted(runs.values()),
            as_timestamp(min(runs)),
            note,
        ),
    )


def activate(cur: psycopg.Cursor[Any], snapshot_id: int) -> int:
    """Turns on only this version. Activating a version with no documents is rejected, because
    otherwise analysis would read an empty corpus with no error raised (the same spot as
    `db/seed/panel.activate`)."""
    cur.execute(SNAPSHOT_COUNT_SQL, (snapshot_id,))
    row = cur.fetchone()
    if not (row and row[0]):
        raise LookupError(f"corpus_document has no rows at snapshot {snapshot_id}; nothing to activate")
    cur.execute(ACTIVATE_SQL, (snapshot_id, snapshot_id))
    return max(cur.rowcount, 0)


def active_snapshot(cur: psycopg.Cursor[Any]) -> int | None:
    """The active snapshot. There cannot be two -- 023's partial unique index blocks that in the DB
    itself."""
    cur.execute(ACTIVE_SQL)
    rows: Sequence[tuple[Any, ...]] = cur.fetchall()
    return int(rows[0][0]) if rows else None


def load(
    conn: psycopg.Connection[Any],
    source_dir: Path,
    *,
    snapshot_id: int = SNAPSHOT_ID,
    label: str = SNAPSHOT_LABEL,
    note: str = SNAPSHOT_NOTE,
    panel_version: int | None = None,
    activate_snapshot: bool = True,
    batch: int = BATCH,
    progress: Progress | None = None,
) -> dict[str, int]:
    """Imports the three CSVs as one snapshot and returns `count(*)` per table."""
    manifest = read_manifest(source_dir)
    runs = runs_by_collected_at(manifest)
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel.active_version(cur)
        if version is None:
            raise CorpusMismatch("no active panel roster; load db/seed --only panel first (fork #31)")
        table_counts = {"channel.csv": check_channels(cur, source_dir, version)}
        # On a re-run that only appends, 0 rows go in, so the insert count cannot say anything about
        # duplicates.
        cur.execute(SNAPSHOT_COUNT_SQL, (snapshot_id,))
        fresh = not (row := cur.fetchone()) or not row[0]
        insert_snapshot(cur, manifest, snapshot_id, label, note)
    conn.commit()

    by_type: Counter[str] = Counter()
    inserted = {}
    inserted["document.csv"] = copy_pages(
        conn,
        DOCUMENT_SQL,
        (
            document_row(row, snapshot_id, runs)
            for row in _tally(read_csv(source_dir / "document.csv"), table_counts, "document.csv", by_type)
        ),
        batch=batch,
        label="corpus_document",
        progress=progress,
    )
    inserted["mention.csv"] = copy_pages(
        conn,
        MENTION_SQL,
        (
            mention_row(row, snapshot_id)
            for row in _tally(read_csv(source_dir / "mention.csv"), table_counts, "mention.csv")
        ),
        batch=batch,
        label="corpus_mention",
        progress=progress,
    )
    # The comparison happens **before** activating: after it, analysis would already be reading that
    # version. The row still stands, but since each snapshot uses a different key (023) it only stands
    # beside the others and overwrites nothing.
    contract.check_counts(manifest, {"table_counts": table_counts, "documents_by_content_type": by_type})
    if fresh:
        contract.check_unique({k: v for k, v in table_counts.items() if k in inserted}, inserted)
    with conn.cursor() as cur:
        # Activating a version means "analysis reads this now", so an import (#38) that only stacks
        # another copy beside an old snapshot has to be callable with activation turned off. The rows
        # are never overwritten either way.
        if activate_snapshot:
            activate(cur, snapshot_id)
        result = counts(cur, TABLES)
    conn.commit()
    return result
