"""Contract test #1: the needs DDL applied (by tool/checks/test) and has every table it declares."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres
DDL_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs"


def declared_columns() -> dict[str, set[str]]:
    # Only the ALTER TABLE ... ADD COLUMN of the later migrations: 001's own columns are covered by
    # every seed INSERT, while an added column has no such witness until a loader fills it.
    added: dict[str, set[str]] = {}
    for path in sorted(DDL_DIR.glob("*.sql")):
        for table, column in re.findall(
            r"ALTER TABLE needs\.(\w+)\s+ADD COLUMN (\w+)", path.read_text(encoding="utf-8")
        ):
            added.setdefault(table, set()).add(column)
    return added


def declared_tables() -> set[str]:
    # sorted(): same filename order db/migrate.sh's `for file in .../*.sql` glob applies them in.
    tables: set[str] = set()
    for path in sorted(DDL_DIR.glob("*.sql")):
        tables |= set(re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8")))
    return tables


def test_the_ddl_declares_the_thirtynine_contract_tables():
    # 20 from 001/002 + 1 from 003_llm_usage.sql (issue #6) + 4 from 004_naver.sql (issue #9:
    # naver_run, naver_fetch_log, naver_datalab_point, naver_blog_post)
    # + 1 from 007_pipeline_stage.sql (upstream issue #138: declaring a pipeline stage's expected interval)
    # + 1 from 008_pipeline_edge.sql (upstream issue #141: what feeds what)
    # + 1 from 020_retrieval_chunk.sql (issue #28; the 020 block is this branch's, see that file)
    # + 3 from 022_panel_and_quarter.sql (fork issue #3: panel_roster, panel_channel,
    # metrics_topic_quarter -- the roster is the parent panel_version points at)
    # + 3 from 023_corpus_snapshot.sql (fork issue #4: corpus_snapshot, corpus_document,
    # corpus_mention -- the snapshot is the parent snapshot_id points at)
    # + 1 from 024_topic_quarter_judgement.sql (fork issue #40: topic_quarter_judgement -- a
    # derivation of metrics_topic_quarter, keyed and FK'd on that table's whole primary key)
    # + 1 from 025_topic_quarter_evidence.sql (fork issue #6: topic_quarter_evidence -- not a
    # derivation but a pointer: the judgement cell's key plus a rank, pointing back at the
    # corpus_document rows that made it).
    # + 1 from 026_retrieval_ask_log.sql (fork issue #73: retrieval_ask_log -- one row per real
    # `cosmai retrieval ask` call, the only query log this schema has).
    # + 2 from 028_mfds_registration.sql (fork issue #55: mfds_snapshot, mfds_registration -- the
    # official MFDS filing ledger as a reference table, plus the one row that says which snapshot of
    # it this is and that it is not updated). 027 adds no table: it is a constraint trigger.
    # The embeddings live in files for now, so there is no table for them yet.
    assert len(declared_tables()) == 39


def test_every_declared_table_exists_in_the_database():
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text("select tablename from pg_tables where schemaname = 'needs'"))
        present = {r[0] for r in rows}
    engine.dispose()
    assert declared_tables() <= present, sorted(declared_tables() - present)


def test_every_added_column_exists_in_the_database():
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        # pg_catalog, not information_schema: the latter hides columns of tables this role holds no
        # privilege on, and needs_migrator holds them only under SET ROLE needs_owner.
        rows = conn.execute(
            text(
                "select c.relname, a.attname from pg_attribute a join pg_class c on c.oid = a.attrelid "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'needs' and a.attnum > 0 and not a.attisdropped"
            )
        )
        present: dict[str, set[str]] = {}
        for table, column in rows:
            present.setdefault(table, set()).add(column)
    engine.dispose()
    missing = {t: sorted(c - present.get(t, set())) for t, c in declared_columns().items()}
    assert not any(missing.values()), missing
