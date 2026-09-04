"""The 4,735 MFDS registration rows go into `needs`, a rerun changes nothing, and the row says which
snapshot it came from (fork #55).

Two things are asked here that a row count cannot answer. First, the ledger is **not updated**, so the
fact that these rows stop at 2026-08-20 has to be readable off the database rather than off this file --
`mfds_snapshot` is where that lives and this test reads it back. Second, the join to our products is
`entp_key` against `entity_lexicon(kind='brand').surface`, measured at 233 of 4,735 rows on 40 brands
(`tool/measure-mfds-join`); the key is a stored column, so the test that matters is that the stored
value is exactly what the pure function says -- one implementation, one witness.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from db import seed
from db.seed import mfds
from db.seed._common import EVAL_DIR, connect, read_csv

SEED_CSV = EVAL_DIR / mfds.MFDS_CSV
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = {"mfds_snapshot": 1, "mfds_registration": 4735}
CSV_COLUMNS = 4
MAX_REPORT_DATE = date(2026, 8, 20)
MIN_REPORT_DATE = date(2008, 10, 30)

SNAPSHOT = (
    "SELECT snapshot_id, label, source_tag, source_file, source_rows, max_report_date,"
    " update_policy, note, loaded_at FROM mfds_snapshot ORDER BY snapshot_id",
    "SELECT report_seq, item_name, entp_name, report_date, entp_key, snapshot_id"
    " FROM mfds_registration ORDER BY report_seq",
)


def _snapshot(cur: psycopg.Cursor[Any]) -> list[list[tuple[Any, ...]]]:
    """Where a rewritten row would be counted -- on counts alone, a rerun whose UPDATE rewrote every
    value still passes green."""
    out = []
    for query in SNAPSHOT:
        cur.execute(query)  # type: ignore[arg-type]
        out.append(cur.fetchall())
    return out


# ---------- the file ----------
def test_the_ledger_csv_is_the_ydc_file_verbatim_and_reads_without_a_bom():
    """`read_csv` opens as utf-8: with a BOM left in, the first column name becomes
    `﻿COSMETIC_REPORT_SEQ` and the loader dies on one KeyError."""
    assert not SEED_CSV.read_bytes().startswith(b"\xef\xbb\xbf")
    csv_rows = read_csv(SEED_CSV)
    assert len(csv_rows) == 4735
    assert len(csv_rows[0]) == CSV_COLUMNS
    assert set(csv_rows[0]) == {"COSMETIC_REPORT_SEQ", "ITEM_NAME", "ENTP_NAME", "report_date"}


def test_the_report_sequence_is_unique_so_it_can_be_the_primary_key():
    """4,736 is the file's line count; the ledger has 4,735 records and the header is the other line."""
    seqs = [row[0] for row in mfds.rows(EVAL_DIR)]
    assert len(seqs) == 4735
    assert len(set(seqs)) == 4735


def test_the_snapshot_facts_are_measured_from_the_file_not_typed_in():
    """The `not updated` decision is only real if the boundary it names is the file's own."""
    facts = mfds.snapshot_facts(mfds.rows(EVAL_DIR))
    assert facts.source_rows == 4735
    assert facts.max_report_date == MAX_REPORT_DATE
    assert facts.min_report_date == MIN_REPORT_DATE


# ---------- the join key is a pure function ----------
# The cases carry Korean company names, so they live in a fixture file rather than in this source
# (tool/checks/lang: Korean a test needs belongs under tests/**/fixtures/). The third column is why
# each case is here, for whoever reads the file next.
COMPANY_KEYS = [(row["company"], row["expected"]) for row in read_csv(FIXTURES / "mfds" / "company_keys.csv")]


@pytest.mark.parametrize(("company", "expected"), COMPANY_KEYS)
def test_the_company_key_drops_the_corporate_form_and_nothing_else(company: str, expected: str):
    assert mfds.normalize_company(company) == expected


def test_the_empty_company_name_folds_to_the_empty_key():
    """The fixture cannot carry this row -- an empty CSV field is indistinguishable from a missing one."""
    assert mfds.normalize_company("") == ""
    assert mfds.normalize_company(None) == ""


def test_the_company_key_is_idempotent():
    """The key is compared against a lexicon surface put through the same function -- if a second pass
    moved, the two sides would depend on how many times each had been normalised."""
    keys = [mfds.normalize_company(row[2]) for row in mfds.rows(EVAL_DIR)]
    assert [mfds.normalize_company(key) for key in keys] == keys


def test_every_company_name_normalises_to_something():
    """An empty key would join every other empty key -- 1,845 companies collapsing onto one row."""
    assert not [row[2] for row in mfds.rows(EVAL_DIR) if not mfds.normalize_company(row[2])]


# ---------- the values go in · a rerun changes nothing ----------
@pytest.mark.postgres
def test_the_seed_loads_4735_registrations_and_a_rerun_changes_nothing(needs_runtime_url: str):
    assert seed.run_all(needs_runtime_url, only=("mfds",)) == EXPECTED
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        before = _snapshot(cur)
    assert seed.run_all(needs_runtime_url, only=("mfds",)) == EXPECTED
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # loaded_at has to match too: an UPDATE with equal values still rewrites the row, and that is
        # not zero changes.
        assert _snapshot(cur) == before


@pytest.mark.postgres
def test_the_snapshot_row_carries_the_origin_the_row_count_and_the_boundary(needs_runtime_url: str):
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT snapshot_id, label, source_tag, source_file, source_rows, max_report_date,"
            " update_policy FROM mfds_snapshot"
        )
        assert cur.fetchall() == [
            (
                mfds.SNAPSHOT_ID,
                mfds.SNAPSHOT_LABEL,
                mfds.SOURCE_TAG,
                mfds.SOURCE_FILE,
                4735,
                MAX_REPORT_DATE,
                mfds.UPDATE_POLICY,
            )
        ]
        cur.execute("SELECT count(*) FROM mfds_registration WHERE snapshot_id <> %s", (mfds.SNAPSHOT_ID,))
        assert cur.fetchone() == (0,)
        cur.execute("SELECT min(report_date), max(report_date) FROM mfds_registration")
        assert cur.fetchone() == (MIN_REPORT_DATE, MAX_REPORT_DATE)


@pytest.mark.postgres
def test_the_stored_company_key_is_exactly_what_the_function_says(needs_runtime_url: str):
    """The column is filled by the loader, not generated by the database -- so the drift this guards
    against is a reload under a changed function, which leaves old keys behind."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT entp_name, entp_key FROM mfds_registration")
        wrong = [(name, key) for name, key in cur.fetchall() if mfds.normalize_company(name) != key]
    assert not wrong


@pytest.mark.postgres
def test_a_second_row_for_the_same_report_sequence_is_refused(needs_runtime_url: str):
    """report_seq is MFDS's own identifier and a filed report is a closed historical fact, so a second
    snapshot must not be able to put the same registration in twice (this is where this table differs
    from 023's corpus, where a re-observation genuinely is a different fact)."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        first = mfds.rows(EVAL_DIR)[0]
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO mfds_registration"
                " (report_seq, item_name, entp_name, report_date, entp_key, snapshot_id)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (first[0], "other", "other", date(2026, 1, 1), "other", mfds.SNAPSHOT_ID),
            )
        conn.rollback()


@pytest.mark.postgres
def test_a_registration_cannot_point_at_a_snapshot_that_does_not_exist(needs_runtime_url: str):
    """Without the FK, "which observation is this row from" is a number the loader promised, and the
    snapshot facts stop being reachable from the row."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO mfds_registration"
                " (report_seq, item_name, entp_name, report_date, entp_key, snapshot_id)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (1, "x", "x", date(2026, 1, 1), "x", 99),
            )
        conn.rollback()


# ---------- the join the contract names ----------
INSERT_SURFACE = (
    "INSERT INTO entity_lexicon (kind, canonical, surface, version, active) VALUES ('brand', %s, %s, 1, true)"
)
SQL_JOIN = (
    "SELECT count(*) FROM mfds_registration m"
    " JOIN entity_lexicon e ON e.surface = m.entp_key AND e.kind = 'brand' AND e.active"
)


@pytest.mark.postgres
def test_the_raw_company_surface_does_not_join_until_the_lexicon_side_is_folded_too(
    needs_runtime_url: str,
):
    """The contract sentence says **both** sides go through `normalize_company`, and this is what makes
    that half necessary rather than decorative: the surface goes in as the ledger writes it, corporate
    form and all, and the equality join finds nothing until the lexicon side is folded as well. On
    production 118 of the 950 active brand surfaces move under the fold, so this is not a corner case.
    """
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # A company whose raw name really does carry a corporate form, so the fold has work to do.
        cur.execute(
            "SELECT entp_name, entp_key FROM mfds_registration"
            " WHERE entp_name <> entp_key ORDER BY report_seq LIMIT 1"
        )
        row = cur.fetchone()
        assert row, "every stored company already equals its key -- pick a different witness"
        raw, key = row
        assert raw != key

        cur.execute(INSERT_SURFACE, (raw, raw))
        cur.execute(SQL_JOIN)
        unfolded = cur.fetchone()

        # The lexicon side folded the way tool/measure-mfds-join folds it -- the fold is Python, not
        # SQL, which is exactly why 028 stores the ledger side rather than generating it.
        cur.execute("SELECT surface FROM entity_lexicon WHERE kind = 'brand' AND active")
        folded = {mfds.normalize_company(surface) for (surface,) in cur.fetchall()}

        cur.execute("SELECT count(*) FROM mfds_registration WHERE entp_key = %s", (key,))
        by_key = cur.fetchone()
        conn.rollback()

    assert unfolded == (0,), "the raw surface joined without the fold -- the witness was already folded"
    assert key in folded
    assert by_key and by_key[0] >= 1


@pytest.mark.postgres
def test_a_folded_surface_joins_the_stored_key_in_one_sql_line(needs_runtime_url: str):
    """Once the lexicon side is folded, the join the contract names is one line of SQL -- which is what
    the stored column buys, and why it is worth a column at all."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT entp_name FROM mfds_registration ORDER BY report_seq LIMIT 1")
        row = cur.fetchone()
        assert row
        surface = mfds.normalize_company(row[0])
        cur.execute(INSERT_SURFACE, (surface, surface))
        cur.execute(SQL_JOIN)
        joined = cur.fetchone()
        conn.rollback()
    assert joined and joined[0] >= 1


# ---------- a refresh is a new snapshot, never a silent merge ----------
@pytest.mark.postgres
def test_loading_a_file_that_disagrees_with_the_stored_snapshot_is_refused(needs_runtime_url: str):
    """Both INSERTs are `ON CONFLICT DO NOTHING` and every registration goes in under a constant
    `SNAPSHOT_ID`, so a grown CSV would drop its new filings into snapshot 1 while the freshly measured
    row count and newest report date were thrown away -- the row that is supposed to say how stale the
    ledger is would go on describing the old file. The loader refuses instead."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    grown = mfds.SnapshotFacts(
        source_rows=4736, min_report_date=MIN_REPORT_DATE, max_report_date=date(2026, 9, 1)
    )
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        before = _snapshot(cur)
        with pytest.raises(ValueError) as raised:
            mfds.check_snapshot(cur, grown)
        conn.rollback()
    # The error has to carry the three values, or the person reading it cannot tell which moved.
    assert "4736" in str(raised.value)
    assert mfds.SOURCE_FILE in str(raised.value)
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        assert _snapshot(cur) == before


@pytest.mark.postgres
def test_the_snapshot_check_passes_on_the_file_it_was_loaded_from(needs_runtime_url: str):
    """The guard has to be silent on the ordinary rerun, or `--only mfds` stops being idempotent."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        mfds.check_snapshot(cur, mfds.snapshot_facts(mfds.rows(EVAL_DIR)))
        conn.rollback()


# ---------- an empty join key is refused ----------
def test_a_company_that_is_only_a_corporate_form_is_refused_before_it_is_loaded(tmp_path: Path):
    """An empty key joins every other empty key, so one such row would stand every brand next to every
    other. The file half of the rule is here; the row half is 028's `CHECK (entp_key <> '')`."""
    source = tmp_path / mfds.MFDS_CSV
    source.parent.mkdir(parents=True)
    header = SEED_CSV.read_text(encoding="utf-8").splitlines()[0]
    only_a_corporate_form = read_csv(FIXTURES / "mfds" / "empty_key.csv")[0]["company"]
    source.write_text(
        f"{header}\n9999999999,x,{only_a_corporate_form},2026-08-20 00:00:00\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="empty join key"):
        mfds.rows(tmp_path)


@pytest.mark.postgres
def test_the_database_refuses_an_empty_key_even_if_the_loader_is_bypassed(needs_runtime_url: str):
    """The CHECK is the half that holds for a hand INSERT, which is the way a bad row actually arrives."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO mfds_registration"
                " (report_seq, item_name, entp_name, report_date, entp_key, snapshot_id)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (9999999999, "x", "x", date(2026, 1, 1), "", mfds.SNAPSHOT_ID),
            )
        conn.rollback()


# ---------- the stored key can be repaired ----------
@pytest.mark.postgres
def test_rekey_repairs_a_stored_key_that_no_longer_matches_the_function(needs_runtime_url: str):
    """`normalize_company` is one implementation, but the key it produces is **stored**, so changing the
    function leaves the loaded rows behind and a rerun cannot fix them (`ON CONFLICT DO NOTHING` touches
    nothing). `rekey` is that repair, and it is why 028 grants UPDATE."""
    seed.run_all(needs_runtime_url, only=("mfds",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # A row left on an older folding, the way a changed function would leave every row.
        cur.execute(
            "UPDATE mfds_registration SET entp_key = 'stale' WHERE report_seq ="
            " (SELECT min(report_seq) FROM mfds_registration) RETURNING report_seq, entp_name"
        )
        row = cur.fetchone()
        assert row
        report_seq, entp_name = row
        conn.commit()

        assert mfds.rekey(cur) == 1
        conn.commit()
        cur.execute("SELECT entp_key FROM mfds_registration WHERE report_seq = %s", (report_seq,))
        assert cur.fetchone() == (mfds.normalize_company(entp_name),)
        # A second pass rewrites no row -- that is what makes it safe to run after any change.
        assert mfds.rekey(cur) == 0
        conn.commit()
