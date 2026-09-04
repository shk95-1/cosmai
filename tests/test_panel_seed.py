"""The 43 channels of the panel roster go into needs, a rerun changes nothing, and there is one active
version (fork #31).

`active` is per row, so two versions can be switched on at once, and then the denominator riding the
`panel_channel (version, panel_role) WHERE active` partial index is counted twice. A partial unique index
cannot express "there is one distinct version among the active rows" (#3 review L6 deleted this invariant
from the loader), so this asks whether the loader carries it.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from db import seed
from db.seed import panel
from db.seed._common import EVAL_DIR, REPO_ROOT, connect, read_csv

SEED_CSV = EVAL_DIR / "panel" / "channels_v1.csv"
SLICE_CSV = REPO_ROOT / "analysis" / "slices" / "ydc" / "seeds" / "channels_v1.csv"
EXPECTED = {"panel_roster": 1, "panel_channel": 43}
ROLES = {"product": 34, "expert": 9}  # contracts/formats.md §패널 명부 CSV 의 v1 패널 열
CSV_COLUMNS = 11  # the file has 11 columns and 6 go into the table (the same contract)

SNAPSHOT = (
    "SELECT version, note, seeded_at FROM panel_roster ORDER BY version",
    "SELECT version, channel_id, panel_role, handle, channel_title, role_basis, source_list,"
    " active, seeded_at FROM panel_channel ORDER BY version, channel_id",
)


def _snapshot(cur: psycopg.Cursor[Any]) -> list[list[tuple[Any, ...]]]:
    """Where a rewritten row is counted -- looking only at counts, a rerun whose UPDATE changed a value
    passes green."""
    out = []
    for query in SNAPSHOT:
        cur.execute(query)  # type: ignore[arg-type]
        out.append(cur.fetchall())
    return out


def _roles(cur: psycopg.Cursor[Any]) -> dict[str, int]:
    cur.execute("SELECT panel_role, count(*) FROM panel_channel WHERE active GROUP BY panel_role")
    return {role: int(n) for role, n in cur.fetchall()}


# ---------- the original lives outside the slice ----------
def test_the_panel_seed_csv_sits_outside_the_slice_that_9_deletes():
    """#9 deleted `analysis/slices/ydc/` -- had the original been left inside it, it would have gone with
    it."""
    assert SEED_CSV.is_file()
    assert not SLICE_CSV.exists()


def test_the_seed_csv_reads_without_a_bom_special_case():
    """`db/seed/_common.read_csv` opens as utf-8 -- with a BOM left, the first column name becomes
    `\ufeffchannel_id` and the loader dies on one KeyError."""
    assert not SEED_CSV.read_bytes().startswith(b"\xef\xbb\xbf")
    csv_rows = read_csv(SEED_CSV)
    assert len(csv_rows) == 43
    assert len(csv_rows[0]) == CSV_COLUMNS
    assert csv_rows[0]["channel_id"].startswith("UC")


# ---------- the values go in · a rerun changes nothing ----------
@pytest.mark.postgres
def test_the_seed_loads_43_panel_channels_and_a_rerun_changes_nothing(needs_runtime_url: str):
    assert seed.run_all(needs_runtime_url, only=("panel",)) == EXPECTED
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        before = _snapshot(cur)
        assert _roles(cur) == ROLES
    assert seed.run_all(needs_runtime_url, only=("panel",)) == EXPECTED
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # seeded_at has to match too: an UPDATE with equal values still rewrites the row, and that is not
        # zero changes.
        assert _snapshot(cur) == before
        assert _roles(cur) == ROLES


@pytest.mark.postgres
def test_the_seed_keeps_the_six_columns_the_contract_maps_and_drops_the_other_five(
    needs_runtime_url: str,
):
    """The five columns the contract says are dropped (team_rank · team_role · channel_published_at ·
    video_count_at_seed · subscriber_count_at_seed) have no place in the table -- only whether the six columns
    were really filled is counted here."""
    seed.run_all(needs_runtime_url, only=("panel",))
    source = {r["channel_id"]: r for r in read_csv(SEED_CSV)}
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT channel_id, panel_role, handle, channel_title, role_basis, source_list"
            " FROM panel_channel WHERE version = %s",
            (panel.PANEL_VERSION,),
        )
        loaded = {row[0]: row for row in cur.fetchall()}
        cur.execute("SELECT version, note FROM panel_roster")
        assert cur.fetchall() == [(panel.PANEL_VERSION, panel.PANEL_NOTE)]
    assert set(loaded) == set(source)
    assert all(
        loaded[cid]
        == (
            cid,
            row["panel_role"],
            row["handle"] or None,
            row["channel_title"],
            row["role_basis"],
            row["source_list"],
        )
        for cid, row in source.items()
    )


# ---------- there is always one active version ----------
@pytest.mark.postgres
def test_a_second_panel_version_turns_the_first_one_off_in_one_statement(needs_runtime_url: str):
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        assert panel.active_version(cur) == panel.PANEL_VERSION
        panel.insert(cur, panel.rows(EVAL_DIR)[:5], version=2, note="seed:test-v2")
        panel.activate(cur, 2)
        conn.commit()
        assert panel.active_version(cur) == 2
        cur.execute("SELECT count(*) FROM panel_channel WHERE active")
        assert cur.fetchone() == (5,)
        # The second activate rewrites no row.
        assert panel.activate(cur, 2) == 0


@pytest.mark.postgres
def test_activating_a_version_that_has_no_rows_is_refused(needs_runtime_url: str):
    """Switch an empty version on and `SET active = (version = n)` turns the whole roster off and makes the
    denominator 0."""
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        with pytest.raises(LookupError):
            panel.activate(cur, 9)
        conn.rollback()
        assert panel.active_version(cur) == panel.PANEL_VERSION


@pytest.mark.postgres
def test_two_active_versions_are_refused_rather_than_counted_twice(needs_runtime_url: str):
    """A partial index cannot stop this state. The place that asks for the denominator stops instead of
    quietly counting 86."""
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        panel.insert(cur, panel.rows(EVAL_DIR), version=2, note="seed:test-v2")
        cur.execute("UPDATE panel_channel SET active = true")
        with pytest.raises(ValueError):
            panel.active_version(cur)
        conn.rollback()
