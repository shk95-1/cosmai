"""storage/tables.py's `metadata` against the schema `trend_radar_schema` actually builds from
contracts/ddl/current/app.trend_radar.sql -- review round 1, #7 Important 2.

The pg-load test (test_pg_load.py) only ever writes through 4 of the 12 tables `metadata` declares;
applying the DDL file to a schema and never reading it back proves the file is valid SQL, not that
this module agrees with it. This is the missing half: reflect the applied schema with SQLAlchemy's
Inspector and diff every table `metadata` knows about -- column names, nullability, and type family
(isinstance against the declared column's own type, which is what tells a Text column from an
Integer one without demanding byte-identical dialect spellings -- `sa.Text` vs the reflected `TEXT`
are the same column, `sa.Text` vs a reflected `INTEGER` are not)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from collectors.commerce.storage.tables import metadata

pytestmark = pytest.mark.postgres


def test_there_are_tables_to_check():
    assert metadata.tables


@pytest.mark.parametrize("table_name", sorted(metadata.tables), ids=lambda t: t)
def test_a_table_matches_the_columns_the_ddl_actually_created(
    trend_radar_schema: str, _schema_name: str, table_name: str
):
    schema = _schema_name
    engine = sa.create_engine(trend_radar_schema)
    try:
        inspector = sa.inspect(engine)
        actual = {c["name"]: c for c in inspector.get_columns(table_name, schema=schema)}
    finally:
        engine.dispose()

    declared = metadata.tables[table_name]
    assert set(actual) == {c.name for c in declared.columns}, (
        f"{table_name}: metadata's columns and the applied DDL's columns disagree"
    )
    for column in declared.columns:
        col = actual[column.name]
        # A primary-key Column is nullable=False by construction (SQLAlchemy sets it, even when the
        # Column() call did not say so), so this already accounts for primary_key -- no separate check.
        assert col["nullable"] == column.nullable, (
            f"{table_name}.{column.name}: nullable={col['nullable']!r} in the DDL, "
            f"{column.nullable!r} in metadata"
        )
        assert isinstance(col["type"], type(column.type)), (
            f"{table_name}.{column.name}: DDL type {col['type']!r} is not a "
            f"{type(column.type).__name__} (metadata declares {column.type!r})"
        )
        actual_type = col["type"]
        if isinstance(column.type, sa.DateTime) and isinstance(actual_type, sa.DateTime):
            assert actual_type.timezone == column.type.timezone, (
                f"{table_name}.{column.name}: timezone-awareness disagrees "
                f"(DDL {actual_type.timezone!r}, metadata {column.type.timezone!r})"
            )
