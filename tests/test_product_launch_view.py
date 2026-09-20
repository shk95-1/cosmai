"""`needs.product_launch_evidence` and the `needs.product_launch` view (#282).

The ledger is the table an axis writes its claims into (#283) and the view is what a reader asks
for the launch interval. The rule table itself is not here -- it is a pure function over claims
(tests/test_launch_rules.py). What this file holds is what only a database can answer: that the
ledger has the shape the contract declares, that an axis can re-state its own claim without
duplicating it, that a claim this rule version could not read cannot be stored at all, and that the
view says exactly what `analysis.launch.launch_interval` says over the same claims. The last one is
why the duplication is allowed to exist: the SQL is a mirror, and a mirror nobody compares is just
a second implementation.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from analysis.launch import EXCLUDED_AXES, launch_interval
from analysis.types import LaunchClaimRow, LaunchIntervalRow

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "product_launch.sql"
DDL = REPO_ROOT / "contracts" / "ddl" / "needs" / "010_product_launch_evidence.sql"
ANON_GRANTS = REPO_ROOT / "db" / "grants" / "postgrest_anon_needs.sql"

AT = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
EXCLUDED = next(iter(EXCLUDED_AXES))
PRODUCTS = ("oy:A1", "oy:A2", "oy:A3", "oy:A4", "oy:A5", "oy:A6")
COLUMNS = (
    "product_ref",
    "axis",
    "direction",
    "claimed_on",
    "claimed_precision",
    "source_ref",
    "match_strength",
    "axis_version",
    "observed_at",
)


def _claim(
    product_ref: str,
    axis: str,
    direction: str,
    claimed_on: date,
    precision: str,
    source_ref: str,
    match: str = "exact",
) -> LaunchClaimRow:
    return LaunchClaimRow(
        product_ref, axis, direction, claimed_on, precision, source_ref, match, "rule-v1.0", AT
    )


# One claim set per product, covering every shape the view has to fold: two claims of one axis on
# one product, both directions, both precisions on both ends, an in-rule `at`, a product whose only
# bound is a lower one (`latest` NULL), and an axis this rule version does not read.
CLAIMS = (
    _claim("oy:A1", "mfds_report", "not_before", date(2026, 6, 12), "day", "seq=1"),
    _claim("oy:A1", "mfds_report", "not_before", date(2026, 5, 2), "day", "seq=2", "partial"),
    _claim("oy:A1", "datalab_onset", "not_after", date(2026, 8, 4), "month", "req=abc"),
    _claim("oy:A1", EXCLUDED, "at", date(2019, 1, 1), "day", "oy:A1"),
    _claim("oy:A2", "old_review", "not_after", date(2019, 3, 2), "day", "oy:A2"),
    _claim("oy:A3", EXCLUDED, "at", date(2026, 9, 1), "day", "oy:A3"),
    # An in-rule `at` moves both ends at once, and it outranks a month-precision lower bound whose
    # own join is the stronger of the two -- the deciding bound is the tightest one, not the surest.
    _claim("oy:A4", "vendor_board", "at", date(2026, 7, 15), "day", "daisomall:99", "partial"),
    _claim("oy:A4", "mfds_report", "not_before", date(2026, 6, 20), "month", "seq=4"),
    _claim("oy:A5", "mfds_report", "not_before", date(2026, 8, 1), "day", "seq=5"),
    # A literal tie: two lower bounds on the same day, differing in join strength. It is the only
    # thing that exercises the SQL's `ORDER BY lower_edge DESC, match_strength ASC` tie-break and
    # the Python's own -- every other row here settles by date alone, so the two could disagree
    # unseen (#282 re-review, fix-when-touched; #283 is the first writer to make two such claims).
    # The weaker one is first so the replacement branch of `launch_interval` has to run.
    _claim("oy:A6", "mfds_report", "not_before", date(2026, 7, 1), "day", "seq=6a", "partial"),
    _claim("oy:A6", "mfds_report", "not_before", date(2026, 7, 1), "day", "seq=6b"),
)

INSERT = (
    "INSERT INTO {schema}.product_launch_evidence (" + ", ".join(COLUMNS) + ")"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _body(path: Path) -> str:
    assert path.exists(), f"{path.name} is not there"
    return path.read_text(encoding="utf-8")


def _values(claim: LaunchClaimRow) -> tuple[object, ...]:
    return tuple(getattr(claim, column) for column in COLUMNS)


@pytest.fixture
def ledger(needs_schema: str, _schema_name: str) -> str:
    """The catalogue rows the ledger's foreign key needs, and nothing else."""
    engine = create_engine(needs_schema)
    with engine.connect() as conn:
        built = conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = :s AND tablename = :t"),
            {"s": _schema_name, "t": "product_launch_evidence"},
        ).scalar()
    assert built == 1, "the needs DDL built no product_launch_evidence"
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            f'INSERT INTO "{_schema_name}".product_ref'
            " (product_ref, name_norm, name, linker_version) VALUES (%s, %s, %s, %s)",
            [(ref, ref, ref, "rule-v1.0") for ref in PRODUCTS],
        )
    engine.dispose()
    return needs_schema


def _columns(url: str, schema: str, relation: str) -> list[tuple[str, bool]]:
    """pg_catalog, not information_schema: the latter hides the columns of a table the connecting
    role holds no privilege on, and needs_migrator holds them only under SET ROLE needs_owner --
    the same reason tests/test_contract_ddl.py reads the catalog directly."""
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT a.attname, a.attnotnull FROM pg_attribute a"
                " JOIN pg_class c ON c.oid = a.attrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relname = :r AND a.attnum > 0 AND NOT a.attisdropped"
                " ORDER BY a.attnum"
            ),
            {"s": schema, "r": relation},
        ).all()
    engine.dispose()
    return [(name, bool(notnull)) for name, notnull in rows]


def test_the_ledger_carries_the_columns_the_contract_declares(ledger: str, _schema_name: str):
    columns = _columns(ledger, _schema_name, "product_launch_evidence")
    assert {name for name, _ in columns} == {*COLUMNS, "note"}
    # `note` is the only thing about a claim that may be absent.
    assert [name for name, notnull in columns if not notnull] == ["note"]


def test_an_axis_restates_its_own_claim_without_duplicating_it(ledger: str, _schema_name: str):
    # The natural key is (product_ref, axis, source_ref): two registrations of one axis on one
    # product are two claims (#283 keeps every match), and the same registration re-read is one.
    engine = create_engine(ledger)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(INSERT.format(schema=f'"{_schema_name}"'), [_values(c) for c in CLAIMS])
        again = dataclasses.replace(CLAIMS[0], claimed_on=date(2026, 6, 30))
        conn.exec_driver_sql(
            INSERT.format(schema=f'"{_schema_name}"')
            + " ON CONFLICT (product_ref, axis, source_ref) DO UPDATE"
            " SET claimed_on = EXCLUDED.claimed_on, observed_at = EXCLUDED.observed_at",
            [_values(again)],
        )
        held = conn.exec_driver_sql(
            f'SELECT count(*), max(claimed_on) FROM "{_schema_name}".product_launch_evidence'
            " WHERE product_ref = 'oy:A1' AND axis = 'mfds_report'"
        ).one()
    engine.dispose()
    assert tuple(held) == (2, date(2026, 6, 30))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("direction", "maybe_before"),
        ("claimed_precision", "quarter"),
        ("match_strength", "probably"),
        ("source_ref", ""),
    ],
)
def test_a_claim_this_rule_version_could_not_read_cannot_be_stored(
    ledger: str, _schema_name: str, column: str, value: str
):
    # A stored row the view does not understand would drop out of the interval silently, and a
    # narrower interval out of less evidence is the one direction this rule table may not move in.
    refused = dataclasses.replace(CLAIMS[0], **{column: value})
    engine = create_engine(ledger)
    with engine.connect() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        with pytest.raises(Exception, match="violates check constraint"):
            conn.exec_driver_sql(INSERT.format(schema=f'"{_schema_name}"'), [_values(refused)])
    engine.dispose()


def test_a_claim_names_a_product_the_catalogue_holds(ledger: str, _schema_name: str):
    stray = dataclasses.replace(CLAIMS[0], product_ref="oy:nobody")
    engine = create_engine(ledger)
    with engine.connect() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        with pytest.raises(Exception, match="violates foreign key constraint"):
            conn.exec_driver_sql(INSERT.format(schema=f'"{_schema_name}"'), [_values(stray)])
    engine.dispose()


def test_the_view_says_what_the_python_rule_module_says(ledger: str, _schema_name: str):
    fields = tuple(f.name for f in dataclasses.fields(LaunchIntervalRow))
    engine = create_engine(ledger)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(INSERT.format(schema=f'"{_schema_name}"'), [_values(c) for c in CLAIMS])
        conn.exec_driver_sql(_body(VIEW).replace("needs.", f'"{_schema_name}".'))
        rows = conn.exec_driver_sql(
            f'SELECT {", ".join(fields)} FROM "{_schema_name}".product_launch ORDER BY product_ref'
        ).all()
    engine.dispose()
    expected = [
        dataclasses.astuple(launch_interval(ref, [c for c in CLAIMS if c.product_ref == ref]))
        for ref in PRODUCTS
    ]
    assert [tuple(row) for row in rows] == expected


def test_a_tie_between_two_lower_bounds_goes_to_the_surer_claim(ledger: str, _schema_name: str):
    # Pinned by value and not only by the mirror: a tie-break both implementations got wrong in the
    # same direction would still make them agree, and `earliest_match` is what rule table row 6 reads.
    fields = tuple(f.name for f in dataclasses.fields(LaunchIntervalRow))
    engine = create_engine(ledger)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(INSERT.format(schema=f'"{_schema_name}"'), [_values(c) for c in CLAIMS])
        conn.exec_driver_sql(_body(VIEW).replace("needs.", f'"{_schema_name}".'))
        row = conn.exec_driver_sql(
            f"SELECT {', '.join(fields)} FROM \"{_schema_name}\".product_launch WHERE product_ref = 'oy:A6'"
        ).one()
    engine.dispose()
    tied = [c for c in CLAIMS if c.product_ref == "oy:A6"]
    assert launch_interval("oy:A6", tied).earliest_match == "exact"
    assert tuple(row) == dataclasses.astuple(launch_interval("oy:A6", tied))


def test_the_view_emits_the_row_type_the_contract_declares(ledger: str, _schema_name: str):
    fields = [f.name for f in dataclasses.fields(LaunchIntervalRow)]
    engine = create_engine(ledger)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(_body(VIEW).replace("needs.", f'"{_schema_name}".'))
    engine.dispose()
    emitted = [name for name, _ in _columns(ledger, _schema_name, "product_launch")]
    assert emitted == fields


def test_the_view_grants_select_to_the_runtime_role_and_to_no_other():
    body = re.sub(r"--[^\n]*", "", _body(VIEW))
    assert set(re.findall(r"GRANT\s+SELECT\s+ON\s+\S+\s+TO\s+(\w+)", body)) == {"needs_runtime"}


def test_the_views_excluded_axis_literal_is_the_rule_modules_constant():
    # The one duplication this pair is allowed: the view has to know which axes the rule version
    # leaves out, and the list is the module's.
    listed = re.search(r"ARRAY\[([^\]]*)\]", _body(VIEW))
    assert listed, "the view names no excluded-axis list"
    assert set(re.findall(r"'([a-z_]+)'", listed.group(1))) == set(EXCLUDED_AXES)


def test_neither_the_ledger_nor_the_view_is_opened_to_anon():
    # contracts/anon_exposure.md: the `needs` section is a whitelist, so a new relation is closed
    # by nobody naming it -- this is what keeps that true as the file grows.
    assert "product_launch" not in _body(ANON_GRANTS)


def test_the_ledger_ddl_sits_in_the_upstream_number_block():
    # contracts/versioning.md: upstream holds 006~019 and the fork holds 020~.
    assert _body(DDL).strip(), "the ledger DDL is empty"
    assert DDL.name.startswith("010_")
