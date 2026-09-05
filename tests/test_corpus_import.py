"""The 2026-08-19 snapshot goes into `needs.corpus_*`, and a recollection laid on top of it does not
overwrite it (fork #4).

The point is not the counts but **not being overwritten**. A recollection (#38) fetches the same video again
under the same unique key (`source + source_item_id`), but the views, likes and comments of 2026-08-19 are
not reproducible -- so if the old observation is overwritten by the new one, the control group of the EPIC
itself is gone. Whether that invariant lives in the key rather than in the loader's discipline is asked here,
along with whether the three `reproduces` numbers of the manifest come out again over the imported set.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

from analysis.retrieval.normalize import normalize_text
from db import corpus, seed
from db.corpus import contract, read_csv, verify
from db.seed import panel
from db.seed._common import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "yt_handoff"
DDL = ROOT / "contracts" / "ddl" / "needs" / "023_corpus_snapshot.sql"
FORMATS = ROOT / "contracts" / "formats.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
README = ROOT / "contracts" / "README.md"

# 픽스처는 손으로 짠 대표 표본이다: 실제 코퍼스(261,317행 · 174M)는 archive/ 에 있고 그 자리는 수정
# 금지이며, 레포에 그 원문을 복사하는 것도 반입 대상이 아니다. 대신 재현 정의가 걸러 내야 하는 다섯
# 가지를 한 벌에 담았다 -- expert 채널 · 쇼츠 · video_unknown · 선크림이 없는 장문 · 그 각각의 댓글.
FIXTURE_REPRODUCES = {"선크림_장문_product": 2, "그_영상_댓글_전체": 3, "그_영상_댓글_중복제외": 2}
FIXTURE_COUNTS = {"corpus_snapshot": 1, "corpus_document": 12, "corpus_mention": 9}

SNAPSHOT = (
    "SELECT snapshot_id, label, produced_by, source_runs, collected_at, note, imported_at"
    " FROM corpus_snapshot ORDER BY snapshot_id",
    "SELECT snapshot_id, source, source_item_id, doc_id, content_type, parent_item_id, channel_id,"
    " published_at, url, text, quality_flags, source_metadata, collected_at, source_run"
    " FROM corpus_document ORDER BY snapshot_id, source, source_item_id",
    "SELECT snapshot_id, doc_id, topic_id, topic_type, trend_use, matched_term, span_start"
    " FROM corpus_mention ORDER BY snapshot_id, doc_id, topic_id",
)


def _rows(cur: psycopg.Cursor[Any], where: str = "") -> list[list[tuple[Any, ...]]]:
    """Where a rewritten row is counted -- looking only at counts, a rerun that overwrote a value passes
    green."""
    out = []
    for query in SNAPSHOT:
        cur.execute(query if not where else query.replace(" ORDER BY", f" WHERE {where} ORDER BY"))  # type: ignore[arg-type]
        out.append(cur.fetchall())
    return out


@pytest.fixture
def seeded(needs_runtime_url: str) -> str:
    """The denominator is the roster: an import needs an active panel roster standing to compare channel
    roles (#31)."""
    seed.run_all(needs_runtime_url, only=("panel",))
    return needs_runtime_url


def _load(url: str, source_dir: Path = FIXTURE, **kwargs: Any) -> dict[str, int]:
    with connect(url) as conn:
        return corpus.load(conn, source_dir, **kwargs)


def _rewritten(tmp_path: Path, source_dir: Path = FIXTURE) -> Path:
    """A recollection holding a different observation under the same unique key. Changed text and views are
    the real shape of #38."""
    out = tmp_path / "recollected"
    out.mkdir()
    for name in ("mention.csv", "manifest.json"):
        (out / name).write_bytes((source_dir / name).read_bytes())
    (out / "channel.csv").write_bytes((source_dir / "channel.csv").read_bytes())
    body = (source_dir / "document.csv").read_text(encoding="utf-8-sig")
    body = body.replace("백탁 진짜 없어요", "백탁 진짜 없어요 (수정됨)").replace('""12000""', '""99999""')
    (out / "document.csv").write_text("﻿" + body, encoding="utf-8")
    return out


# ---------- import ----------
@pytest.mark.postgres
def test_the_snapshot_loads_and_a_rerun_changes_nothing(seeded: str):
    assert _load(seeded) == FIXTURE_COUNTS
    with connect(seeded) as conn, conn.cursor() as cur:
        before = _rows(cur)
    assert _load(seeded) == FIXTURE_COUNTS
    with connect(seeded) as conn, conn.cursor() as cur:
        # imported_at has to match too: an UPDATE with equal values still rewrites the row, and that is not
        # zero changes.
        assert _rows(cur) == before


@pytest.mark.postgres
def test_every_document_carries_the_run_and_the_moment_it_was_observed(seeded: str):
    """That it is a snapshot is readable from the row: which run it came from and when it was gathered are in
    columns rather than inside JSON."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        cur.execute("SELECT source_run, count(*) FROM corpus_document GROUP BY source_run ORDER BY 1")
        assert cur.fetchall() == [("run_20260819T053057Z", 5), ("run_20260819T054559Z", 7)]
        cur.execute("SELECT count(*) FROM corpus_document WHERE collected_at IS NULL")
        assert cur.fetchone() == (0,)
        cur.execute("SELECT source_runs, collected_at::date FROM corpus_snapshot")
        runs, day = cur.fetchone()  # type: ignore[misc]
        assert runs == ["run_20260819T053057Z", "run_20260819T054559Z"]
        assert str(day) == "2026-08-19"


@pytest.mark.postgres
def test_doc_id_is_the_two_key_columns_joined_by_a_colon(seeded: str):
    """The second sentence of rule 1 is a generated column -- built by the loader, two doc_id sets could
    drift."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM corpus_document WHERE doc_id <> source || ':' || source_item_id")
        assert cur.fetchone() == (0,)
        with pytest.raises(psycopg.errors.GeneratedAlways):
            cur.execute("UPDATE corpus_document SET doc_id = 'x'")
        conn.rollback()


@pytest.mark.postgres
def test_a_mention_without_its_document_is_refused(seeded: str):
    """0 orphan mentions is a value the manifest counted, and here the DB carries it."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO corpus_mention (snapshot_id, doc_id, topic_id, topic_type, trend_use)"
                " VALUES (1, 'youtube_video:NOPE', '선크림', 'product_category', false)"
            )
        conn.rollback()


# ---------- it is not overwritten ----------
@pytest.mark.postgres
def test_a_later_snapshot_with_the_same_unique_key_leaves_the_first_untouched(seeded: str, tmp_path: Path):
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        before = _rows(cur, "snapshot_id = 1")
    _load(seeded, _rewritten(tmp_path), snapshot_id=2, label="recollected", activate_snapshot=False)
    with connect(seeded) as conn, conn.cursor() as cur:
        assert _rows(cur, "snapshot_id = 1") == before
        cur.execute("SELECT text FROM corpus_document WHERE snapshot_id = 2 AND source_item_id = 'CMT_A1_1'")
        assert cur.fetchone() == ("백탁 진짜 없어요 (수정됨)",)
        cur.execute("SELECT count(*) FROM corpus_document")
        assert cur.fetchone() == (24,)


@pytest.mark.postgres
def test_only_one_snapshot_can_be_active_at_a_time(seeded: str, tmp_path: Path):
    """With two active versions, two observations read as one population. The partial unique index of 023
    stops that."""
    _load(seeded)
    _load(seeded, _rewritten(tmp_path), snapshot_id=2, label="recollected", activate_snapshot=False)
    with connect(seeded) as conn, conn.cursor() as cur:
        assert corpus.active_snapshot(cur) == 1
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("UPDATE corpus_snapshot SET active = true WHERE snapshot_id = 2")
        conn.rollback()
        corpus.activate(cur, 2)
        conn.commit()
        assert corpus.active_snapshot(cur) == 2
        assert corpus.activate(cur, 2) == 0


@pytest.mark.postgres
def test_activating_a_snapshot_with_no_documents_is_refused(seeded: str):
    """Switch an empty version on and the analysis reads an empty corpus with no error."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        with pytest.raises(LookupError):
            corpus.activate(cur, 9)
        conn.rollback()
        assert corpus.active_snapshot(cur) == 1


# ---------- reproduction check ----------
@pytest.mark.postgres
def test_the_three_reproduction_numbers_come_out_of_the_loaded_rows(seeded: str):
    _load(seeded)
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    with connect(seeded) as conn:
        assert verify.reproduce(conn) == FIXTURE_REPRODUCES
    assert manifest["reproduces"] == FIXTURE_REPRODUCES


@pytest.mark.postgres
def test_the_reproduction_reads_the_panel_through_the_active_roster(seeded: str):
    """Had it been a bare `WHERE active`, two versions would double the denominator and this number would grow
    quietly (#31 review)."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        panel.insert(cur, panel.rows(ROOT / "eval"), version=2, note="seed:test-v2")
        panel.activate(cur, 2)
        conn.commit()
    with connect(seeded) as conn:
        assert verify.reproduce(conn) == FIXTURE_REPRODUCES


# ---------- it refuses what is out of step with the contract ----------
@pytest.mark.postgres
def test_a_channel_the_active_roster_gives_another_role_is_refused(seeded: str, tmp_path: Path):
    """A role is the value that sets the denominator, so it lives in one table only. channel.csv does not
    become a table; it is only compared against."""
    source = _rewritten(tmp_path)
    body = (FIXTURE / "channel.csv").read_text(encoding="utf-8-sig")
    (source / "channel.csv").write_text("﻿" + body.replace(",product,", ",expert,", 1), encoding="utf-8")
    with connect(seeded) as conn, pytest.raises(corpus.CorpusMismatch, match="panel roster"):
        corpus.load(conn, source, snapshot_id=3, label="bad-roles")


@pytest.mark.postgres
def test_a_manifest_that_declares_more_rows_than_arrive_is_refused(seeded: str, tmp_path: Path):
    """A truncated CSV simply has fewer rows and no error -- and then every ratio of this snapshot changes
    quietly."""
    source = _rewritten(tmp_path)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["table_counts"]["document.csv"] += 1
    (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with connect(seeded) as conn, pytest.raises(contract.ManifestMismatch, match="table_counts"):
        corpus.load(conn, source, snapshot_id=4, label="short-load")


@pytest.mark.postgres
def test_a_snapshot_whose_counts_do_not_match_never_becomes_the_active_one(seeded: str, tmp_path: Path):
    """The refusal comes before the switch to active -- after it, the analysis is already reading that
    version."""
    _load(seeded)
    source = _rewritten(tmp_path)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["documents_by_content_type"]["comment"] -= 1
    (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with connect(seeded) as conn, pytest.raises(contract.ManifestMismatch):
        corpus.load(conn, source, snapshot_id=5, label="wrong-mix")
    with connect(seeded) as conn, conn.cursor() as cur:
        assert corpus.active_snapshot(cur) == 1


@pytest.mark.postgres
def test_the_declared_counts_are_the_counts_the_load_reads(seeded: str):
    """If the fixture writes its own row count wrong, the suite ends up validating a different corpus."""
    _load(seeded)
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["table_counts"] == {"document.csv": 12, "mention.csv": 9, "channel.csv": 3}
    assert manifest["documents_by_content_type"] == {
        "video_long": 4,
        "video_short": 1,
        "video_unknown": 1,
        "comment": 6,
    }


@pytest.mark.postgres
def test_a_file_that_carries_the_same_unique_key_twice_is_refused(seeded: str, tmp_path: Path):
    """ON CONFLICT DO NOTHING drops duplicates quietly -- uncounted, that is indistinguishable from a
    truncated input. The manifest's `input_counts.duplicate_docs = 0` is proved here."""
    source = _rewritten(tmp_path)
    lines = (source / "document.csv").read_text(encoding="utf-8-sig").splitlines(keepends=True)
    (source / "document.csv").write_text("\ufeff" + "".join([*lines, lines[1]]), encoding="utf-8")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    # The declared row count is raised with it -- otherwise check_counts catches it first and the duplicate
    # is never tested.
    manifest["table_counts"]["document.csv"] += 1
    manifest["documents_by_content_type"]["video_long"] += 1
    (source / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with connect(seeded) as conn, pytest.raises(contract.ManifestMismatch, match="duplicate"):
        corpus.load(conn, source, snapshot_id=6, label="dup-key")


@pytest.mark.postgres
def test_a_comment_whose_parent_video_is_absent_is_not_refused_by_the_database(seeded: str):
    """It would be false for the contract to say the DB carries `orphan_comments`: the parent of a comment is
    parent_item_id and there is no FK there (023 only puts a partial index on it). What the FK carries is the
    orphan **mention** side."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus_document (snapshot_id, source, source_item_id, content_type,"
            " parent_item_id, channel_id, published_at, text, collected_at, source_run)"
            " VALUES (1, 'youtube_comment', 'CMT_ORPHAN', 'comment', 'VID_DOES_NOT_EXIST',"
            " 'UCqrNqg3UgVoD3Sa-F_TxuSA', now(), '', now(), 'run_20260819T053057Z')"
        )
        assert cur.rowcount == 1
        conn.rollback()


def test_the_contract_does_not_claim_a_foreign_key_for_orphan_comments():
    assert "`orphan_comments` is not carried by the DB" in FORMATS.read_text(encoding="utf-8")


def test_the_count_rule_is_a_sentence_in_the_formats_contract():
    """With the rule living only in the code, the next person cannot tell it from "it was left out"."""
    assert "선언한 행수와 반입분을 대조한다" in FORMATS.read_text(encoding="utf-8")


def test_a_manifest_whose_rules_differ_is_refused(tmp_path: Path):
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    manifest["rules"] = list(manifest["rules"])[:-1]
    with pytest.raises(contract.ManifestMismatch, match="rules"):
        contract.check(manifest)


def test_the_fixture_manifest_carries_the_same_rules_the_archive_manifest_does():
    """If the fixture carries a different rule from the contract, this suite validates something other than
    the real corpus."""
    contract.check(json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8")))


# ---------- the sentence is in the contract ----------
@pytest.mark.parametrize("rule", contract.RULES)
def test_every_manifest_rule_is_a_sentence_in_the_formats_contract(rule: str):
    assert rule in FORMATS.read_text(encoding="utf-8")


@pytest.mark.parametrize("limitation", contract.LIMITATIONS)
def test_every_manifest_limitation_is_a_sentence_in_the_interfaces_contract(limitation: str):
    assert limitation in INTERFACES.read_text(encoding="utf-8")


def test_the_text_rule_is_a_sentence_in_the_formats_contract():
    assert contract.TEXT_RULE in FORMATS.read_text(encoding="utf-8")


def test_the_loaded_text_is_already_a_fixed_point_of_this_repos_normalizer():
    """ydc's normalize_text runs once and cosmai's runs to the fixed point. The two implementations differ, so
    text could mean different things here, but on this corpus one round is already the fixed point -- 0 of the
    measured 261,317 rows change. If the fixture loses that property (a sample with an escape left in it goes
    in), it is caught here."""
    texts = [row["text"] for row in read_csv(FIXTURE / "document.csv")]
    assert texts and all(normalize_text(t) == t for t in texts)


# ---------- the DDL lives in this fork's number block ----------
def test_the_ddl_lives_in_this_forks_number_block():
    assert re.fullmatch(r"02[0-9]_\w+\.sql", DDL.name)


def test_the_contracts_index_carries_a_row_for_this_ddl():
    assert f"ddl/needs/{DDL.name}" in README.read_text(encoding="utf-8")


@pytest.mark.postgres
@pytest.mark.parametrize("table", corpus.TABLES)
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_the_runtime_role_may_write_the_new_tables(table: str, privilege: str):
    """The import runs as needs_runtime -- a missing GRANT fails only the production load. A per-test schema
    opens ALL TABLES wholesale so the omission is invisible, which is why the real deployed `needs` is looked
    at."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET ROLE needs_owner"))  # outside SET ROLE, migrator has no USAGE on needs
            allowed = conn.execute(
                text("select has_table_privilege('needs_runtime', :t, :p)"),
                {"t": f"needs.{table}", "p": privilege},
            ).scalar_one()
    finally:
        engine.dispose()
    assert allowed is True


@pytest.mark.postgres
def test_a_content_type_outside_the_vocabulary_is_refused(seeded: str):
    """One typo quietly opening a separate family gives that family a denominator of its own (the same
    sentence as 022)."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE corpus_document SET content_type = 'video_longform'")
        conn.rollback()
