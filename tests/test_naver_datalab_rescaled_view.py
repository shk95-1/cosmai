"""`needs.naver_datalab_rescaled`: the anchor rescale of #90, computed at read time.

A DataLab ratio is scaled to 0-100 inside one request alone (contracts/formats.md, NAVER DataLab
section), so two requests' ratios are two different scales. Since #90 every request carries the one
global anchor keyword, and dividing a row by the anchor row of its own `request_key` and month is
what puts the two on a common scale. This measures the three answers that view owes: a comparable
number where an anchor exists, NULL where it does not, and NULL where the anchor is 0.

The rescale is a view rather than an analysis stage because it is a pure function of rows already
stored -- the same shape as `collector_health` and `pipeline_health`, and the same deploy path
(db/migrate.sh step (f)).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from collectors.naver import scope

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "naver_datalab_rescaled.sql"
FORMATS = REPO_ROOT / "contracts" / "formats.md"

AT = datetime(2026, 9, 6, 6, 10, tzinfo=UTC)
ANCHOR = scope.DATALAB_ANCHOR
# Two requests of one run: different categories, so their anchor rows do not share a primary key
# (category, group_key, month) -- which is exactly how the collector's batches land.
K1 = "a" * 64
K2 = "b" * 64
# A third boundary with no anchor row of its own: the batch whose anchor row was never written
# (#248, e.g. the response carried no anchor point at all -- the case #250 guards against).
K3 = "c" * 64

# (category, group_key, month, ratio, request_key). The anchor row is still stored here too (#90's
# readers), but since #248 the view no longer joins through it -- see ANCHORS below.
POINTS = (
    ("sun", ANCHOR, "2016-01", 50.0, K1),
    ("sun", "haze", "2016-01", 25.0, K1),  # half the anchor
    ("sun", "sting", "2016-01", 100.0, K1),  # twice the anchor
    ("lip", ANCHOR, "2016-01", 10.0, K2),
    ("lip", "haze", "2016-01", 5.0, K2),  # half the anchor of another request, another raw ratio
    ("sun", "haze", "2016-02", 40.0, K1),  # a month the anchor has no point for
    ("sun", ANCHOR, "2016-03", 0.0, K1),
    ("sun", "haze", "2016-03", 7.0, K1),  # an anchor of 0 divides nothing
    ("sun", "flat", "2016-01", 30.0, K3),  # a request whose anchor row was never written
    ("sun", "null", "2016-01", None, K1),  # a point the vendor gave no ratio for
)

# (request_key, month, ratio) -- needs.naver_datalab_anchor, one row per request and month (#248).
# No row for K1/2016-02 (the month the anchor has no point for) or for K3 (no anchor at all).
ANCHORS = (
    (K1, "2016-01", 50.0),
    (K1, "2016-03", 0.0),
    (K2, "2016-01", 10.0),
)


@pytest.fixture
def rescaled(
    needs_schema: str, needs_runtime_url: str, _schema_name: str
) -> dict[tuple[str, str, str], object]:
    """In production db/migrate.sh (f) applies this file as needs_owner; here it goes into the
    per-test schema the same way the other view tests do it."""
    engine = create_engine(needs_schema)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            f'INSERT INTO "{_schema_name}".naver_datalab_point'
            " (category, group_key, month, ratio, terms, request_key, captured_at)"
            " VALUES (%s, %s, %s, %s, '[]'::jsonb, %s, %s)",
            [(c, g, m, r, k, AT) for c, g, m, r, k in POINTS],
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{_schema_name}".naver_datalab_anchor (request_key, month, ratio, captured_at)'
            " VALUES (%s, %s, %s, %s)",
            [(k, m, r, AT) for k, m, r in ANCHORS],
        )
        conn.exec_driver_sql(VIEW.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    engine.dispose()

    reader = create_engine(needs_runtime_url)
    with reader.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT category, group_key, month, ratio, anchor_ratio, ratio_rescaled, request_key"
                " FROM naver_datalab_rescaled"
            )
        ).all()
    reader.dispose()
    return {(r[0], r[1], r[2]): r for r in rows}


def _value(row: object) -> float | None:
    rescaled = row[5]  # type: ignore[index]
    return None if rescaled is None else float(rescaled)


def test_two_requests_rescale_to_comparable_values(rescaled):
    # The point of the anchor: raw 25.0 and raw 5.0 come from two requests with two different 100s,
    # and both are half of their own request's anchor -- so both read 0.5.
    assert _value(rescaled[("sun", "haze", "2016-01")]) == 0.5
    assert _value(rescaled[("lip", "haze", "2016-01")]) == 0.5
    assert _value(rescaled[("sun", "sting", "2016-01")]) == 2.0
    # The raw ratios stay on the row, untouched -- the collector stores no rescaled number (#44).
    assert float(rescaled[("sun", "haze", "2016-01")][3]) == 25.0
    assert float(rescaled[("lip", "haze", "2016-01")][3]) == 5.0


def test_the_anchor_row_itself_reads_as_one(rescaled):
    assert _value(rescaled[("sun", ANCHOR, "2016-01")]) == 1.0


def test_a_month_the_anchor_has_no_point_for_is_null(rescaled):
    row = rescaled[("sun", "haze", "2016-02")]
    assert row[4] is None
    assert row[5] is None


def test_an_anchor_of_zero_is_null_rather_than_an_error(rescaled):
    row = rescaled[("sun", "haze", "2016-03")]
    assert row[4] == Decimal("0")
    assert row[5] is None


def test_a_row_whose_request_left_no_anchor_is_null(rescaled):
    # #248: the NULL rule now guards a request with no naver_datalab_anchor row at all (K3), not
    # a batch whose anchor was overwritten -- since every request's own anchor row survives, that
    # overwrite case no longer exists.
    assert _value(rescaled[("sun", "flat", "2016-01")]) is None


def test_a_point_with_no_ratio_stays_null(rescaled):
    assert rescaled[("sun", "null", "2016-01")][3] is None
    assert rescaled[("sun", "null", "2016-01")][5] is None


def test_every_stored_point_keeps_a_row(rescaled):
    # A LEFT JOIN, not an INNER one: a row that cannot be rescaled must still be readable, or the
    # view silently drops what the collector wrote.
    assert len(rescaled) == len(POINTS)


def test_the_view_joins_the_anchor_table_on_request_key_and_month():
    """#248: the join no longer carries a SQL literal of the anchor's name -- the collector alone
    decides which group is the anchor and writes it into needs.naver_datalab_anchor, keyed by the
    request boundary. A literal group_key match would be exactly the shape that caused the bug."""
    sql = VIEW.read_text(encoding="utf-8")
    assert "needs.naver_datalab_anchor" in sql
    assert f"'{scope.DATALAB_ANCHOR}'" not in sql
    # The sibling views' convention: the reading role is granted in the view's own file, because
    # db/migrate.sh drops and recreates the view on every deploy (#158).
    assert "GRANT SELECT ON needs.naver_datalab_rescaled TO needs_runtime;" in sql


def test_the_contract_names_the_anchor_table():
    # #248: formats.md must say which table the anchor comes from, or a reader has no way to find it.
    text_ = FORMATS.read_text(encoding="utf-8")
    assert "needs.naver_datalab_anchor" in text_
