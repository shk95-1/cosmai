"""The MFDS registration ledger -> `needs.mfds_snapshot` + `needs.mfds_registration` (fork #55).

    uv run python -m db.seed --only mfds

4,735 filings copied verbatim from ydc v0.4.0's `rag/mfds_items.csv` into `eval/mfds/mfds_items_v1.csv`.
It is a reference table, not a collection target: the rows are the official filing record, so nothing
here re-derives them and **nothing updates them**. The copy stops at the newest report date in the
file and `mfds_snapshot` carries that boundary, the origin tag and `loaded_at` so a reader can see how
stale the ledger is without leaving the database (issue #55 work item 3).

The join to our products is `entp_key` against `needs.entity_lexicon(kind='brand').surface`, both
sides folded by `normalize_company` below. `uv run tool/measure-mfds-join` re-measures it.

Two consequences of both INSERTs being `ON CONFLICT DO NOTHING`, written down because they are
assumptions rather than guarantees. A filing that changed under the same `report_seq` is neither
re-entered nor updated by a rerun -- this table assumes MFDS does not re-file under a report number
it has already used, and if that assumption breaks the repair is a new snapshot, not a rerun. And a
change to `normalize_company` does not reach the stored `entp_key`; `rekey` below is that repair.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.seed._common import as_date, counts, read_csv

TABLES = ("mfds_snapshot", "mfds_registration")

SNAPSHOT_ID = 1
SNAPSHOT_LABEL = "mfds-ydc-v0.4.0"
SOURCE_TAG = "ydc v0.4.0 (76db718)"
SOURCE_FILE = "eval/mfds/mfds_items_v1.csv"
# 'not updated' is the decision, and it is a value rather than prose so a query can find it.
UPDATE_POLICY = "not_updated"
SNAPSHOT_NOTE = "MFDS cosmetic registration ledger; reference table, not re-collected (fork #55)"
MFDS_CSV = Path("mfds") / "mfds_items_v1.csv"
# rows() emits this order, and INSERT_SQL's placeholders follow it.
COLUMNS = ("report_seq", "item_name", "entp_name", "report_date", "entp_key")

# The corporate form is removed before the two sides are compared: 4,332 of the 4,735 companies carry
# one and no brand surface in entity_lexicon does, so leaving it in drops the join from 233 rows to 1.
# NFKC runs first, which folds the single-character ㈜ into (주) -- the pattern needs one spelling, not two.
# The latin forms are bounded on **both** sides and the Korean ones on neither. Both halves of that are
# load-bearing: unbounded, `inc` also lives inside `Incospharm Corp` and `INCELLDERM` and `corp` inside
# `Corporate Beauty`, so a company would be folded to `ospharm`/`ellderm`/`oratebeauty` and could
# collide with a real brand; bounded, `주식회사` would match nothing, because it is written glued to
# the name as often as not (`주식회사바임`).
CORPORATE_FORMS = re.compile(
    r"주식회사|유한회사|유한책임회사|\(주\)|\(유\)"
    r"|\bco\.?,?\s*ltd\b\.?|\bcorp(?:oration)?\b\.?|\binc\b\.?",
    re.IGNORECASE,
)
# Everything that is not a Hangul syllable, a latin letter or a digit. Spacing and punctuation are the
# part of a company name that two records never agree on.
NON_KEY = re.compile(r"[^0-9a-z가-힣]+")

INSERT_SNAPSHOT_SQL: LiteralString = """
INSERT INTO mfds_snapshot
  (snapshot_id, label, source_tag, source_file, source_rows, max_report_date, update_policy, note)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id) DO NOTHING
"""
# DO NOTHING, like the dictionaries: a filed registration is closed history, so a rerun that found a
# different value would mean the source changed under us, not that this row should be rewritten.
INSERT_SQL: LiteralString = """
INSERT INTO mfds_registration
  (report_seq, item_name, entp_name, report_date, entp_key, snapshot_id)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (report_seq) DO NOTHING
"""
# Read back after the DO NOTHING so a stored snapshot row can be compared with the file in hand.
STORED_SNAPSHOT_SQL: LiteralString = """
SELECT source_rows, max_report_date, source_file FROM mfds_snapshot WHERE snapshot_id = %s
"""
# Recomputing a stored key is an UPDATE, and the WHERE is what makes a no-op rerun rewrite no row.
REKEY_SQL: LiteralString = """
UPDATE mfds_registration SET entp_key = %s WHERE report_seq = %s AND entp_key IS DISTINCT FROM %s
"""
ALL_KEYS_SQL: LiteralString = "SELECT report_seq, entp_name, entp_key FROM mfds_registration"


def fold(value: str | None) -> str:
    """NFKC, lower-cased, everything that is not a Hangul syllable, a latin letter or a digit dropped.

    The half of the key that is not about companies at all -- `tool/measure-mfds-join` folds product
    names with it when it re-measures the candidate this schema rejected.
    """
    return NON_KEY.sub("", unicodedata.normalize("NFKC", value or "").lower())


def normalize_company(name: str | None) -> str:
    """The company name folded to its join key: `fold`, with the corporate form taken off first.

    Pure and idempotent -- the lexicon surface it is compared against goes through the same call, so a
    second pass that moved would make the answer depend on how many times each side had been folded.
    """
    return fold(CORPORATE_FORMS.sub(" ", unicodedata.normalize("NFKC", name or "")))


@dataclass(frozen=True)
class SnapshotFacts:
    """What this copy of the ledger is, measured off the file rather than typed in."""

    source_rows: int
    min_report_date: date
    max_report_date: date


def rows(source_dir: Path) -> list[tuple[Any, ...]]:
    """One CSV line -> one row in COLUMNS order. The report date carries a 00:00:00 time in the source
    and no filing has an hour, so it is stored as a date.

    A company that folds to the empty key is refused here rather than stored: an empty key joins every
    other empty key, so one such row would put every brand in the lexicon next to every other. 028
    carries the same rule as `CHECK (entp_key <> '')`, and this is the half that can name the company.
    """
    ledger = [
        (
            int(r["COSMETIC_REPORT_SEQ"]),
            r["ITEM_NAME"],
            r["ENTP_NAME"],
            as_date(r["report_date"]),
            normalize_company(r["ENTP_NAME"]),
        )
        for r in read_csv(source_dir / MFDS_CSV)
    ]
    empty = [(row[0], row[2]) for row in ledger if not row[4]]
    if empty:
        raise ValueError(
            f"{len(empty)} company name(s) fold to the empty join key and cannot be loaded: {empty[:5]}"
        )
    return ledger


def snapshot_facts(ledger: Sequence[Sequence[Any]]) -> SnapshotFacts:
    """The boundary the "not updated" decision names, read off the rows that are about to be loaded --
    so the row in the database and the file on disk cannot say different things."""
    dates = [row[3] for row in ledger]
    if not dates:
        raise LookupError(f"{MFDS_CSV} has no rows; there is no snapshot to record")
    return SnapshotFacts(source_rows=len(dates), min_report_date=min(dates), max_report_date=max(dates))


def check_snapshot(cur: psycopg.Cursor[Any], facts: SnapshotFacts) -> None:
    """Refuse to load into a snapshot row that describes a different file.

    `INSERT ... ON CONFLICT DO NOTHING` on the snapshot plus a constant `SNAPSHOT_ID` on every
    registration is a silent merge waiting to happen: point the loader at a **grown** CSV and the new
    filings land under snapshot 1 while the freshly measured `source_rows` and `max_report_date` are
    thrown away, so the row that is supposed to say how stale the ledger is would describe the old
    file. Refusing here makes a refresh what it should be -- a reviewed change that bumps
    `SNAPSHOT_ID` and `SNAPSHOT_LABEL` in code and lands the new filings under their own snapshot.
    """
    cur.execute(STORED_SNAPSHOT_SQL, (SNAPSHOT_ID,))
    stored = cur.fetchone()
    if not stored:
        return
    if tuple(stored) != (facts.source_rows, facts.max_report_date, SOURCE_FILE):
        raise ValueError(
            f"mfds_snapshot {SNAPSHOT_ID} holds (source_rows, max_report_date, source_file) "
            f"{tuple(stored)!r} but the file in hand measures "
            f"{(facts.source_rows, facts.max_report_date, SOURCE_FILE)!r}. "
            "A grown or replaced ledger is a new snapshot: bump SNAPSHOT_ID and SNAPSHOT_LABEL."
        )


def insert(cur: psycopg.Cursor[Any], ledger: Sequence[Sequence[Any]], facts: SnapshotFacts) -> int:
    """The snapshot row first: the ledger rows point at it by FK, so the parent has to stand first."""
    cur.execute(
        INSERT_SNAPSHOT_SQL,
        (
            SNAPSHOT_ID,
            SNAPSHOT_LABEL,
            SOURCE_TAG,
            SOURCE_FILE,
            facts.source_rows,
            facts.max_report_date,
            UPDATE_POLICY,
            SNAPSHOT_NOTE,
        ),
    )
    # After the DO NOTHING, not before: on a first load the row this compares against is the one just
    # written, and on a rerun it is the one already there.
    check_snapshot(cur, facts)
    if not ledger:
        return 0
    cur.executemany(INSERT_SQL, [(*row, SNAPSHOT_ID) for row in ledger])
    return max(cur.rowcount, 0)


def rekey(cur: psycopg.Cursor[Any]) -> int:
    """Recompute `entp_key` for every stored row and return how many actually moved.

    `normalize_company` is the single implementation of the join key, but the key is **stored**, so a
    change to that function leaves the loaded rows on the old folding -- and a plain rerun cannot
    repair them, because both INSERTs are `ON CONFLICT DO NOTHING`. This is the repair path, and it is
    why 028 grants `UPDATE` rather than `SELECT, INSERT` alone. It is not called by `load`: a re-key
    is a consequence of changing the function, so it is run deliberately.
    """
    cur.execute(ALL_KEYS_SQL)
    moved = 0
    for report_seq, entp_name, entp_key in cur.fetchall():
        recomputed = normalize_company(entp_name)
        if recomputed != entp_key:
            cur.execute(REKEY_SQL, (recomputed, report_seq, recomputed))
            moved += max(cur.rowcount, 0)
    return moved


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    ledger = rows(source_dir)
    insert(cur, ledger, snapshot_facts(ledger))
    return counts(cur, TABLES)
