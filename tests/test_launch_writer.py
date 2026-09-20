"""`cosmai analyze launch`: what the axes write into the evidence ledger, and what they withdraw (#283).

The rules themselves are `tests/test_launch_axes.py`. What only a database can answer is here: that
the stage writes one claim per axis and per matched source row, that a second run over unchanged
sources adds nothing, and that a **membership move withdraws** the claims left on a `product_ref`
nothing points at any more — without touching an axis this stage does not write, which is the one
way a scoped DELETE can go wrong and empty somebody else's evidence.

The product and company names the rows need are Korean, so they are data under
`tests/fixtures/launch/` rather than literals here (`tool/checks/lang`).
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from analysis.launch import axes
from analysis.launch import pipeline as launch_stage
from db.seed._common import connect

pytestmark = pytest.mark.postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "launch"
AT = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
BOARD_DAY = date(2026, 8, 20)
OLD_REVIEW_DAY = date(2019, 3, 2)
FIRST_SEEN = date(2026, 8, 17)
# An axis this stage does not write, planted to prove the withdrawal is scoped.
FOREIGN_AXIS = "datalab_onset"
CLAIM_COLUMNS = "product_ref, axis, direction, claimed_on, claimed_precision, source_ref, match_strength"


def _rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


SURFACES = _rows("brand_surfaces.csv")
REGISTRATIONS = {row["case"]: row for row in _rows("registrations.csv")}
CATALOGUE = {row["case"]: row for row in _rows("catalogue.csv")}
TITLES = {row["case"]: row for row in _rows("titles.csv")}

HERE = CATALOGUE["sunscreen"]["product_ref"]  # the ref the members point at first
THERE = CATALOGUE["latin"]["product_ref"]  # the ref a re-clustering moves them to
ANCHOR = ("oliveyoung", "A1")
FOLLOWER = ("daisomall", "D1")


@pytest.fixture
def sources(needs_schema: str, trend_radar_schema: str, needs_runtime_url: str) -> str:
    """Both schemas in one throwaway schema (`commerce_schema=''` below), the needs side owned by
    needs_owner as in production and the commerce side opened to needs_runtime with SELECT alone."""
    engine = create_engine(needs_schema)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO product (source, product_key, captured_at, name, brand, first_seen_at,"
            " last_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (*ANCHOR, AT, TITLES["bracketed_latin"]["title"], HERE, FIRST_SEEN, AT),
                (*FOLLOWER, AT, TITLES["no_tag"]["title"], HERE, FIRST_SEEN, AT),
            ],
        )
        conn.exec_driver_sql(
            "INSERT INTO new_product (source, product_key, captured_at, name) VALUES (%s, %s, %s, %s)",
            [(*FOLLOWER, BOARD_DAY, TITLES["no_tag"]["title"])],
        )
        conn.exec_driver_sql(
            "INSERT INTO review (source, review_key, captured_at, product_key, written_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            [("oliveyoung", "R1", AT, ANCHOR[1], OLD_REVIEW_DAY)],
        )
        conn.exec_driver_sql("GRANT SELECT ON product, new_product, review TO needs_runtime")
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            "INSERT INTO product_ref (product_ref, brand, name_norm, name, linker_version)"
            " VALUES (%s, %s, %s, %s, 'rule-v1.0')",
            [
                (row["product_ref"], row["brand"], row["name_norm"], row["name_norm"])
                for row in (CATALOGUE["sunscreen"], CATALOGUE["latin"])
            ],
        )
        conn.exec_driver_sql(
            "INSERT INTO product_member (source, product_key, product_ref, role) VALUES (%s, %s, %s, %s)",
            [(*ANCHOR, HERE, "primary"), (*FOLLOWER, HERE, "member")],
        )
        conn.exec_driver_sql(
            "INSERT INTO entity_lexicon (kind, canonical, surface, version) VALUES (%s, %s, %s, 1)",
            [("brand", row["canonical"], row["surface"]) for row in SURFACES],
        )
        conn.exec_driver_sql(
            "INSERT INTO mfds_snapshot (snapshot_id, label, source_tag, source_file, source_rows,"
            " max_report_date, update_policy) VALUES (1, 'test', 'test', 'test', 1, %s, 'not_updated')",
            (date(2026, 8, 20),),
        )
        conn.exec_driver_sql(
            "INSERT INTO mfds_registration (report_seq, item_name, entp_name, report_date, entp_key,"
            " snapshot_id) VALUES (%s, %s, %s, %s, %s, 1)",
            [
                (
                    int(REGISTRATIONS[case]["report_seq"]),
                    REGISTRATIONS[case]["item_name"],
                    REGISTRATIONS[case]["entp_key"],
                    date.fromisoformat(REGISTRATIONS[case]["report_date"]),
                    REGISTRATIONS[case]["entp_key"],
                )
                for case in ("base", "variant", "other_line")
            ],
        )
        conn.exec_driver_sql(
            "INSERT INTO product_launch_evidence (product_ref, axis, direction, claimed_on,"
            " claimed_precision, source_ref, match_strength, axis_version, observed_at)"
            " VALUES (%s, %s, 'not_after', %s, 'month', 'req=abc', 'exact', 'rule-v1.0', %s)",
            [(HERE, FOREIGN_AXIS, date(2026, 8, 1), AT)],
        )
    engine.dispose()
    return needs_runtime_url


def _ledger(url: str, where: str = "") -> list[tuple]:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT {CLAIM_COLUMNS} FROM product_launch_evidence {where} ORDER BY 1, 2, 6")
        ).all()
    engine.dispose()
    return [tuple(row) for row in rows]


def _run(url: str) -> dict[str, int]:
    with connect(url) as conn:
        return launch_stage.run(conn, commerce_schema="", observed_at=AT)


def test_the_stage_writes_one_claim_per_axis_and_per_matched_source_row(sources: str):
    counts = _run(sources)
    written = [row for row in _ledger(sources) if row[1] != FOREIGN_AXIS]
    assert written == [
        # Two registrations match the line: the exact one and the later variant, and both are kept
        # because the rule table and not the axis picks the bound.
        (HERE, axes.MFDS, "not_before", date(2021, 4, 16), "day", "2021013803", "exact"),
        (HERE, axes.MFDS, "not_before", date(2023, 11, 2), "day", "2023038252", "partial"),
        # The oldest held review: an upper bound, and `exact` because the ref was minted from that
        # very listing (`role = 'primary'`).
        (HERE, axes.OLD_REVIEW, "not_after", OLD_REVIEW_DAY, "day", "oliveyoung:A1", "exact"),
        # The board: an upper bound at the capture, `partial` because that listing reached the ref
        # through the linker's cross-site match rather than by being its anchor.
        (HERE, axes.VENDOR_BOARD, "not_after", BOARD_DAY, "day", "daisomall:D1", "partial"),
        (HERE, axes.VENDOR_TITLE_TAG, "not_after", FIRST_SEEN, "day", "oliveyoung:A1", "exact"),
    ]
    assert counts["launch_claims"] == 5
    assert counts["launch_withdrawn"] == 0


def test_the_recorded_title_tag_keeps_the_tag_it_matched(sources: str):
    _run(sources)
    engine = create_engine(sources)
    with engine.connect() as conn:
        note = conn.execute(
            text("SELECT note FROM product_launch_evidence WHERE axis = :axis"),
            {"axis": axes.VENDOR_TITLE_TAG},
        ).scalar()
    engine.dispose()
    assert note == TITLES["bracketed_latin"]["tag"]


def test_a_listing_with_no_vendor_tag_makes_no_tag_claim(sources: str):
    _run(sources)
    tagged = [row for row in _ledger(sources) if row[1] == axes.VENDOR_TITLE_TAG]
    assert [row[5] for row in tagged] == ["oliveyoung:A1"]


def test_a_second_run_over_unchanged_sources_writes_nothing_new(sources: str):
    _run(sources)
    first = _ledger(sources)
    counts = _run(sources)
    assert _ledger(sources) == first
    assert counts["launch_withdrawn"] == 0


def test_a_membership_move_withdraws_this_stages_claims_and_leaves_another_axis_alone(sources: str):
    _run(sources)
    engine = create_engine(sources)
    with engine.begin() as conn:
        # What a re-clustering does: `product_member.product_ref` is re-pointed while the old
        # `product_ref` row stays standing (`analysis/linker/pipeline.py` MEMBER_SQL).
        conn.execute(text("UPDATE product_member SET product_ref = :there"), {"there": THERE})
    engine.dispose()
    counts = _run(sources)

    left = _ledger(sources, f"WHERE product_ref = '{HERE}'")
    assert [row[1] for row in left] == [FOREIGN_AXIS], "only the axis this stage writes is withdrawn"
    moved = _ledger(sources, f"WHERE product_ref = '{THERE}'")
    assert {row[1] for row in moved} == {
        axes.MFDS,
        axes.OLD_REVIEW,
        axes.VENDOR_BOARD,
        axes.VENDOR_TITLE_TAG,
    }
    assert counts["launch_withdrawn"] == 5


def test_a_ref_nothing_points_at_any_more_holds_no_claim_of_this_stage(sources: str):
    _run(sources)
    engine = create_engine(sources)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM product_member"))
    engine.dispose()
    counts = _run(sources)
    assert [row[1] for row in _ledger(sources)] == [FOREIGN_AXIS]
    assert (counts["launch_claims"], counts["launch_withdrawn"]) == (0, 5)
