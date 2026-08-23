"""storage/tables.py's `metadata` against the schema `tubedepth_schema` actually builds from
contracts/ddl/current/app.tubedepth.sql + contracts/ddl/tubedepth/001_*.sql -- issue #8's completion
bar ("DDL 재덤프 diff = 추가 컬럼 2개뿐"), same method as collectors/commerce's test of this name."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from collectors.youtube.storage.tables import metadata

pytestmark = pytest.mark.postgres


def test_there_are_tables_to_check():
    assert metadata.tables


@pytest.mark.parametrize("table_name", sorted(metadata.tables), ids=lambda t: t)
def test_a_table_matches_the_columns_the_ddl_actually_created(
    tubedepth_schema: str, _schema_name: str, table_name: str
):
    schema = _schema_name
    engine = sa.create_engine(tubedepth_schema)
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


def test_the_only_new_columns_are_the_two_issue_8_added(tubedepth_schema: str, _schema_name: str):
    engine = sa.create_engine(tubedepth_schema)
    try:
        actual = {c["name"] for c in sa.inspect(engine).get_columns("comments", schema=_schema_name)}
    finally:
        engine.dispose()
    assert actual - set(metadata.tables["comments"].columns.keys()) == set()
    assert {"published_at_resolution", "channel_is_brand_owner"} <= actual
