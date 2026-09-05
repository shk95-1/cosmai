"""Contract test: the panel population and quarter granularity -- the contract sentence and the 022 DDL
say the same thing (fork #3).

The moment month and quarter coexist in one schema, two questions attach to every row: **which
granularity is this**, and **a ratio over which population**. With a different denominator, the same
code produces a number with a different meaning, with no error raised. So this checks the source of
truth (which table owns which granularity), the roster (where a panel role lives), and the denominator
(is it readable from the row) against each other, in both the document and the DDL.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
DDL_DIR = ROOT / "contracts" / "ddl" / "needs"
DDL = DDL_DIR / "022_panel_and_quarter.sql"
FORMATS = ROOT / "contracts" / "formats.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"

ROSTER = "panel_roster"
PANEL = "panel_channel"
QUARTER = "metrics_topic_quarter"
VIEW = ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql"
TOPIC_AXIS = ROOT / "analysis" / "retrieval" / "dict" / "topics_v1.csv"
NEED_REGISTRY = ROOT / "eval" / "lexicon" / "need_key_v1.csv"
PANEL_SEED = ROOT / "eval" / "panel" / "channels_v1.csv"
GRAIN_HEADER = "| grain | canonical table | the row's time slot |"
ROLE_HEADER = "| `panel_role` | meaning | v1 panel |"
PANEL_CSV_HEADER = "| CSV column | → `needs.panel_channel` |"
PANEL_CHANNELS = 43  # ydc's 43 seed channels (issue #3's body). Loaded by tests/test_panel_seed.py (#31).
# The registry for the topic axis. This sentence has to appear identically in two spots of the contract
# (the two tests below).
REGISTRY = "aspect_lexicon(ruleset='retrieval-topic')"
NOT_THE_REGISTRY = "not needs.need_key"
# The table belongs to needs_owner -- migrator only ever stands in that spot through SET ROLE
# (db/bootstrap.sql).
OWNER = text("SET ROLE needs_owner")


def _ddl() -> str:
    return DDL.read_text(encoding="utf-8")


def _create_block(table: str) -> str:
    found = re.search(rf"CREATE TABLE needs\.{table} \((.*?)\n\);", _ddl(), re.DOTALL)
    assert found, f"022 declares no needs.{table}"
    return found.group(1)


def _columns(table: str) -> dict[str, str]:
    """Column name -> declaration (type and constraint)."""
    columns: dict[str, str] = {}
    for line in _create_block(table).splitlines():
        stripped = re.sub(r"\s+--.*", "", line).strip().rstrip(",")
        # A column name can start with a keyword (`unique_ratio`) -- a constraint line is split by
        # word boundary.
        if re.match(r"(PRIMARY KEY|UNIQUE|CHECK|FOREIGN)\b", stripped, re.IGNORECASE):
            continue
        named = re.match(r"([a-z_]+)\s+(.+)", stripped)
        if named:
            columns[named.group(1)] = named.group(2)
    return columns


def _primary_key(table: str) -> list[str]:
    found = re.search(r"PRIMARY KEY \(([^)]*)\)", _create_block(table))
    assert found, f"needs.{table} has no PRIMARY KEY"
    return [name.strip() for name in found.group(1).split(",")]


def _markdown_rows(path: Path, header: str) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    for line in lines[lines.index(header) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _grain_rows() -> list[tuple[str, str]]:
    """(grain, canonical table) — `contracts/formats.md` §Time."""
    return [
        (cells[0], cells[1].strip("`").removeprefix("needs."))
        for cells in _markdown_rows(FORMATS, GRAIN_HEADER)
    ]


def _declared_tables() -> set[str]:
    return {
        table
        for path in DDL_DIR.glob("*.sql")
        for table in re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8"))
    }


def test_the_contract_names_the_owner_of_each_grain():
    """The sentence this task most easily drops -- once the same concept of a metric lives in two
    tables, unless the contract says which table is the source of truth for which granularity, the two
    quietly become substitutes for each other."""
    assert _grain_rows() == [("month", "metrics_need"), ("month", "metrics_wish"), ("quarter", QUARTER)]


def test_no_table_is_the_owner_of_two_grains():
    tables = [table for _, table in _grain_rows()]
    assert len(tables) == len(set(tables))


def test_every_metrics_table_in_the_ddl_declares_its_grain():
    # The moment a new aggregate table stands with no grain declared, this question reopens.
    assert {t for t in _declared_tables() if t.startswith("metrics_")} == {t for _, t in _grain_rows()}


def _quarter_formulas() -> set[str]:
    body = INTERFACES.read_text(encoding="utf-8")
    return set(re.findall(rf"^- \*\*(\w+)\*\* \(`{QUARTER}`\)", body, re.MULTILINE))


def test_every_ratio_column_of_the_quarter_table_has_a_formula_in_the_contract():
    """A ratio or log value is never reproduced without a definition. A count column (int) has what
    it's counting in its own name, but a `numeric` column needs the contract to say what its numerator
    and denominator are."""
    ratios = {name for name, declaration in _columns(QUARTER).items() if declaration.startswith("numeric")}
    assert ratios == _quarter_formulas()


def test_the_quarter_row_carries_the_population_it_is_a_ratio_of():
    """If the denominator cannot be told from the row, this issue has failed (issue #3's body)."""
    columns = set(_columns(QUARTER))
    assert {"panel_version", "panel_role", "denom_channels", "documents", "quarter_mentions"} <= columns


def test_the_population_is_part_of_the_key_not_a_footnote():
    # With the population outside the key, two populations contend for the same slot, and the later one
    # overwrites the earlier.
    assert {"panel_version", "panel_role"} <= set(_primary_key(QUARTER))


def _roles_in_ddl(table: str) -> tuple[str, ...]:
    found = re.search(r"panel_role\s+text NOT NULL CHECK \(panel_role IN \(([^)]*)\)\)", _create_block(table))
    assert found, f"needs.{table}.panel_role has no role vocabulary"
    return tuple(value.strip().strip("'") for value in found.group(1).split(","))


@pytest.mark.parametrize("table", [PANEL, QUARTER])
def test_the_roles_the_ddl_accepts_are_the_roles_the_contract_explains(table: str):
    explained = tuple(cells[0].strip("`") for cells in _markdown_rows(FORMATS, ROLE_HEADER))
    assert _roles_in_ddl(table) == explained


def test_the_two_roles_add_up_to_the_seeded_panel():
    """The 43 channels are the denominator. Without the contract naming that number, no one can tell
    what #31 filled in fully."""
    counted = [int(cells[2]) for cells in _markdown_rows(FORMATS, ROLE_HEADER)]
    assert sum(counted) == PANEL_CHANNELS
    assert f"{PANEL_CHANNELS} channels" in FORMATS.read_text(encoding="utf-8")


def test_the_ddl_lives_in_this_forks_number_block():
    # upstream is 006~019, the fork is 020~ (contracts/versioning.md). Up through 021 is applied in
    # production.
    assert DDL.name.startswith("022_")
    # What this catches is not "is it the highest number" but **a number collision**: since the ledger's
    # (needs.schema_migration) key is the file name, whichever of two files sharing a number comes
    # second is quietly skipped by the deploy.
    numbers = [name.split("_", 1)[0] for name in (path.name for path in DDL_DIR.glob("02*.sql"))]
    assert len(numbers) == len(set(numbers)), sorted(numbers)


def test_the_contracts_index_carries_a_row_for_this_ddl():
    rows = [line for line in INDEX.read_text(encoding="utf-8").splitlines() if DDL.name in line]
    assert len(rows) == 1, rows
    checkers = [c.strip("`") for c in re.findall(r"`[^`]+`", rows[0]) if c.strip("`").endswith(".py")]
    assert checkers, rows[0]
    for checker in checkers:
        assert (ROOT / checker).exists(), checker


@pytest.mark.postgres
@pytest.mark.parametrize("table", [ROSTER, PANEL, QUARTER])
def test_the_table_stands_in_the_applied_schema_with_its_declared_columns(
    needs_schema: str, _schema_name: str, table: str
):
    engine = create_engine(needs_schema)
    try:
        applied = {c["name"] for c in inspect(engine).get_columns(table, schema=_schema_name)}
    finally:
        engine.dispose()
    assert set(_columns(table)) == applied


@pytest.mark.postgres
def test_a_role_outside_the_vocabulary_is_refused(needs_schema: str):
    """A channel not on the roster is outside the panel and never enters the denominator -- for that to
    hold, there must be no room for "something role-like" to get in (user decision 2026-08-26)."""
    engine = create_engine(needs_schema)
    insert = text(
        f"INSERT INTO {PANEL} (channel_id, version, panel_role) VALUES ('UC0', 1, :role)"  # noqa: S608
    )
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)  # the role that made the table; needs_migrator has none outside SET ROLE
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
            conn.execute(insert, {"role": "product"})
        with pytest.raises(Exception, match="panel_role"):
            with engine.begin() as conn:
                conn.execute(OWNER)
                conn.execute(insert, {"role": "beauty"})
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_quarter_column_only_accepts_a_quarter_literal(needs_schema: str):
    """If `'2026-07'` gets in, that row looks like a month row while using a quarter's denominator --
    grammar is exactly what the grain is."""
    engine = create_engine(needs_schema)
    insert = text(
        f"INSERT INTO {QUARTER} (run_id, scope, topic_key, quarter, source, content_type, "  # noqa: S608
        "panel_version, panel_role, mentions, documents, quarter_mentions, denom_channels, sample_ok) "
        "VALUES (:run, '선블록', '백탁', :quarter, 'youtube_video', 'long_form', 1, 'product', "
        "5, 10, 5, 34, true)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
            run = conn.execute(
                text("INSERT INTO analysis_run (versions) VALUES ('{}'::jsonb) RETURNING run_id")
            ).scalar_one()
            conn.execute(insert, {"run": run, "quarter": "2026Q3"})
        with pytest.raises(Exception, match="quarter"):
            with engine.begin() as conn:
                conn.execute(OWNER)
                conn.execute(insert, {"run": run, "quarter": "2026-07"})
    finally:
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize("table", [ROSTER, PANEL, QUARTER])
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_the_runtime_role_may_write_the_new_tables(table: str, privilege: str):
    """The per-test schema opens ALL TABLES wholesale, so a missing GRANT never shows there -- this
    looks at the `needs` schema the real deploy actually made (`db/migrate.sh`)."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(OWNER)  # needs_migrator has no USAGE on the needs schema outside SET ROLE
            allowed = conn.execute(
                text("select has_table_privilege('needs_runtime', :t, :p)"),
                {"t": f"needs.{table}", "p": privilege},
            ).scalar_one()
    finally:
        engine.dispose()
    assert allowed is True


# ---------- The topic axis is not the need axis (review B5) ----------
def _csv_column(path: Path, column: str) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row[column] for row in csv.DictReader(handle)]


def test_the_topic_axis_and_the_need_key_registry_are_not_the_same_vocabulary():
    """If the contract wrote 'the vocabulary is the same', a `USING (need_key)` join would return one
    topic and quietly drop the rest. This checks the two axes directly against each other, right here,
    so that sentence can never be written again."""
    topics = set(_csv_column(TOPIC_AXIS, "aspect"))  # the retrieval-topic axis (15 topics)
    registry = set(_csv_column(NEED_REGISTRY, "need_key"))  # the needs.need_key registry (38 keys)
    assert len(topics) > 10 and len(registry) > 30, (len(topics), len(registry))
    assert topics & registry == {"백탁"}, sorted(topics & registry)


def test_the_quarter_table_names_its_topic_column_topic_key():
    """Naming it `need_key` would read as joinable with no FK at all -- the name has to say what axis
    it is."""
    columns = _columns(QUARTER)
    assert "topic_key" in columns
    assert "need_key" not in columns
    assert "topic_key" in _primary_key(QUARTER)


@pytest.mark.parametrize("path", [DDL, INTERFACES])
def test_the_contract_names_the_registry_the_topic_key_comes_from(path: Path):
    body = path.read_text(encoding="utf-8")
    assert REGISTRY in body, f"{path.name} 이 주제 축의 레지스트리를 말하지 않는다"
    assert NOT_THE_REGISTRY in body, f"{path.name} 이 need 축과의 구분을 말하지 않는다"


# ---------- Stored digits (review M1) ----------
def _numeric_scales() -> dict[str, int]:
    scales: dict[str, int] = {}
    for name, declaration in _columns(QUARTER).items():
        found = re.match(r"numeric\((\d+),\s*(\d+)\)", declaration)
        if declaration.startswith("numeric"):
            assert found, f"{name} 은 맨 numeric 이라 저장이 자리수를 지키지 않는다: {declaration}"
            scales[name] = int(found.group(2))
    return scales


def test_the_stored_digits_are_the_digits_the_contract_pins():
    """The judgment threshold (ydc `judge.py`'s `TAU`, `DIFFUSION_TAU`) is a number fitted on top of a
    rounded value -- the digit count is exactly that gate's resolution, so the contract and the DDL must
    carry the same digit count."""
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    pinned = next(line for line in lines if line.strip().startswith("Decimal places:"))
    assert _numeric_scales() == {name: int(digits) for name, digits in re.findall(r"`(\w+)` (\d+)", pinned)}


# ---------- A closed vocabulary inside the key (review M2, L3) ----------
def _vocabulary_in_ddl(table: str, column: str) -> tuple[str, ...]:
    found = re.search(rf"{column}\s+text NOT NULL CHECK \({column} IN \(([^)]*)\)\)", _create_block(table))
    assert found, f"needs.{table}.{column} 은 키 안인데 어휘가 열려 있다"
    return tuple(value.strip().strip("'") for value in found.group(1).split(","))


@pytest.mark.parametrize("column", ["source", "content_type"])
def test_the_closed_vocabularies_in_the_key_are_the_ones_the_contract_explains(column: str):
    """A single typo (`youtube_videos`) quietly opens a separate group inside the key -- exactly the
    thing `formats.md` says is impossible. The type comment and the CHECK must carry the same
    vocabulary."""
    comment = re.search(rf"^    {column}: str  # ([^—]+)", INTERFACES.read_text(encoding="utf-8"), re.M)
    assert comment, f"interfaces.md 의 dataclass 가 {column} 의 어휘를 말하지 않는다"
    assert _vocabulary_in_ddl(QUARTER, column) == tuple(v.strip() for v in comment.group(1).split("|"))


def test_the_sample_gate_is_one_number_in_the_ddl_and_the_contract():
    """`sample_ok` is NOT NULL, yet no definition existed anywhere in the contract. The sample gate is
    the same number as velocity's condition, so if that number disagrees between the two spots, the row
    says something other than its own name."""
    found = re.search(r"CHECK \(sample_ok = \(mentions >= (\d+)\)\)", _create_block(QUARTER))
    assert found, "sample_ok 의 정의가 DDL 에 없다"
    assert f"`mentions >= {found.group(1)}`" in INTERFACES.read_text(encoding="utf-8")


# ---------- A parent for the population pointer to point at (review M3) ----------
def test_the_population_pointer_has_a_parent_it_can_point_at():
    """`panel_channel`'s PK is `(version, channel_id)`, so an FK could never be put on `version`
    alone. Only with a one-row parent does "an old row keeps what it used as its denominator" become an
    enforced sentence."""
    assert _primary_key(ROSTER) == ["version"]
    assert "REFERENCES needs.panel_roster" in _columns(PANEL)["version"]
    assert "REFERENCES needs.panel_roster" in _columns(QUARTER)["panel_version"]


# ---------- The panel CSV spec and the real file (review M4) ----------
def test_the_panel_csv_spec_lists_the_columns_the_seed_file_actually_has():
    """#31 loads this file. If the spec has 6 columns but the file has 11, no one can tell what the
    loader dropped."""
    header = PANEL_SEED.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    spec = [cells[0].strip("`") for cells in _markdown_rows(FORMATS, PANEL_CSV_HEADER)]
    assert spec == header


# ---------- Two invariants of the row set (review B2, B4) ----------
# (topic, quarter, mentions, quarter_mentions). A dense grid of 2 trend_use topics x 2 quarters -- a
# zero-mention cell is still a row. quarter_mentions is the sum of mentions across that quarter's rows.
GRID = [
    ("백탁", "2025Q1", 6, 10), ("발림성", "2025Q1", 4, 10),
    ("백탁", "2025Q2", 0, 5), ("발림성", "2025Q2", 5, 5),
]  # fmt: skip
# Mixing in a trend_use=false topic makes that quarter's sum exceed the denominator -- the grid stays a
# rectangle, so a count alone would not show it.
OFF_AXIS = [("추천_재구매", "2025Q1", 3, 10), ("추천_재구매", "2025Q2", 4, 5)]

ROW = text(
    f"INSERT INTO {QUARTER} (run_id, scope, topic_key, quarter, source, content_type, "  # noqa: S608
    "panel_version, panel_role, mentions, documents, quarter_mentions, denom_channels, sample_ok) "
    "VALUES (:run, 'all', :topic, :quarter, 'youtube_video', 'long_form', 1, 'product', "
    ":mentions, 20, :qm, 34, :ok)"
)


def _seed(conn: Connection, rows: list[tuple[str, str, int, int]], run: int) -> None:
    for topic, quarter, mentions, quarter_mentions in rows:
        conn.execute(
            ROW,
            {
                "run": run,
                "topic": topic,
                "quarter": quarter,
                "mentions": mentions,
                "qm": quarter_mentions,
                "ok": mentions >= 5,
            },  # fmt: skip
        )


def _violations(conn: Connection) -> set[tuple[str, str | None]]:
    rows = conn.execute(text(f"SELECT violation, quarter FROM {QUARTER}_violation"))  # noqa: S608
    return {(r[0], r[1]) for r in rows}


@pytest.mark.postgres
def test_the_view_catches_a_sparse_grid_and_a_denominator_that_does_not_close(
    needs_schema: str, _schema_name: str
):
    """Left as a contract sentence alone, no one catches #5 if it drops a zero-cell or mixes in a topic
    outside `trend_use`. Both invariants are things SQL can ask again over the stored rows, and this
    view is that question."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.exec_driver_sql(VIEW.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
            run = conn.execute(
                text("INSERT INTO analysis_run (versions) VALUES ('{}'::jsonb) RETURNING run_id")
            ).scalar_one()

            _seed(conn, GRID, run)
            assert _violations(conn) == set()  # a dense grid + a closed denominator = nothing to report

            _seed(conn, OFF_AXIS, run)
            assert _violations(conn) == {
                ("quarter_mentions_not_closed", "2025Q1"),
                ("quarter_mentions_not_closed", "2025Q2"),
            }

            conn.execute(text(f"DELETE FROM {QUARTER} WHERE topic_key = '추천_재구매'"))  # noqa: S608
            conn.execute(  # dropping a zero-mention cell raises persistence's baseline along with it
                text(f"DELETE FROM {QUARTER} WHERE topic_key = '백탁' AND quarter = '2025Q2'")  # noqa: S608
            )
            assert _violations(conn) == {("sparse_grid", None)}
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_roster_version_a_metric_row_points_at_cannot_be_deleted(needs_schema: str):
    """`formats.md` says "even after the panel changes, an old row keeps what it used as its
    denominator". A roster row may be deleted, but the version must survive for that sentence to hold
    true -- needs_runtime holds DELETE."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
            conn.execute(
                text(
                    f"INSERT INTO {PANEL} (channel_id, version, panel_role) "  # noqa: S608
                    "VALUES ('UC0', 1, 'product')"
                )
            )
            run = conn.execute(
                text("INSERT INTO analysis_run (versions) VALUES ('{}'::jsonb) RETURNING run_id")
            ).scalar_one()
            _seed(conn, [("백탁", "2025Q1", 6, 6)], run)
            conn.execute(text(f"DELETE FROM {PANEL} WHERE version = 1"))  # noqa: S608
        with pytest.raises(Exception, match="panel_roster"):
            with engine.begin() as conn:
                conn.execute(OWNER)
                conn.execute(text(f"DELETE FROM {ROSTER} WHERE version = 1"))  # noqa: S608
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_migrate_sh_leaves_the_invariant_view_in_the_needs_schema_for_needs_runtime():
    """Without the view deployed, #5 has nowhere to ask its question -- this looks at the `needs` the
    real deploy made."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(OWNER)
            allowed = conn.execute(
                text("select has_table_privilege('needs_runtime', :v, 'SELECT')"),
                {"v": f"needs.{QUARTER}_violation"},
            ).scalar_one()
    finally:
        engine.dispose()
    assert allowed is True
