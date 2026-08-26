"""2026-08-19 스냅샷이 `needs.corpus_*` 로 들어가고, 재수집분이 그 위에 얹혀도 덮지 않는다 (포크 #4).

핵심은 개수가 아니라 **덮이지 않음**이다. 재수집(#38)은 같은 유일키(`source + source_item_id`)로 같은
영상을 다시 가져오지만 2026-08-19 의 조회수·좋아요·댓글은 재현되지 않는다 -- 그래서 옛 관측이 새
관측에 덮이면 EPIC 의 대조군 자체가 사라진다. 그 불변식이 적재기 규율이 아니라 키에 있는지를 여기서
묻고, 매니페스트의 `reproduces` 세 숫자가 반입분 위에서 다시 나오는지도 함께 센다.
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
    """행이 다시 쓰였는지 세는 자리 -- 개수만 보면 값을 덮은 재실행이 초록으로 지나간다."""
    out = []
    for query in SNAPSHOT:
        cur.execute(query if not where else query.replace(" ORDER BY", f" WHERE {where} ORDER BY"))  # type: ignore[arg-type]
        out.append(cur.fetchall())
    return out


@pytest.fixture
def seeded(needs_runtime_url: str) -> str:
    """분모는 명부다: 반입은 활성 패널 명부가 서 있어야 채널 역할을 대조할 수 있다 (#31)."""
    seed.run_all(needs_runtime_url, only=("panel",))
    return needs_runtime_url


def _load(url: str, source_dir: Path = FIXTURE, **kwargs: Any) -> dict[str, int]:
    with connect(url) as conn:
        return corpus.load(conn, source_dir, **kwargs)


def _rewritten(tmp_path: Path, source_dir: Path = FIXTURE) -> Path:
    """같은 유일키에 다른 관측을 담은 재수집분. 텍스트와 조회수가 바뀐 것이 #38 의 실제 모양이다."""
    out = tmp_path / "recollected"
    out.mkdir()
    for name in ("mention.csv", "manifest.json"):
        (out / name).write_bytes((source_dir / name).read_bytes())
    (out / "channel.csv").write_bytes((source_dir / "channel.csv").read_bytes())
    body = (source_dir / "document.csv").read_text(encoding="utf-8-sig")
    body = body.replace("백탁 진짜 없어요", "백탁 진짜 없어요 (수정됨)").replace('""12000""', '""99999""')
    (out / "document.csv").write_text("﻿" + body, encoding="utf-8")
    return out


# ---------- 반입 ----------
@pytest.mark.postgres
def test_the_snapshot_loads_and_a_rerun_changes_nothing(seeded: str):
    assert _load(seeded) == FIXTURE_COUNTS
    with connect(seeded) as conn, conn.cursor() as cur:
        before = _rows(cur)
    assert _load(seeded) == FIXTURE_COUNTS
    with connect(seeded) as conn, conn.cursor() as cur:
        # imported_at 까지 같아야 한다: 값이 같은 UPDATE 도 행을 다시 쓰고, 그것은 변경 0 이 아니다.
        assert _rows(cur) == before


@pytest.mark.postgres
def test_every_document_carries_the_run_and_the_moment_it_was_observed(seeded: str):
    """스냅샷임이 행에서 읽힌다: 어느 런에서 왔고 언제 걷힌 값인지가 JSON 안이 아니라 칸에 있다."""
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
    """규칙 1 의 뒷문장은 생성 열이다 -- 적재기가 만들면 두 벌의 doc_id 가 갈릴 수 있다."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM corpus_document WHERE doc_id <> source || ':' || source_item_id")
        assert cur.fetchone() == (0,)
        with pytest.raises(psycopg.errors.GeneratedAlways):
            cur.execute("UPDATE corpus_document SET doc_id = 'x'")
        conn.rollback()


@pytest.mark.postgres
def test_a_mention_without_its_document_is_refused(seeded: str):
    """고아 언급 0 은 매니페스트가 세어 둔 값이고, 여기서는 DB 가 진다."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO corpus_mention (snapshot_id, doc_id, topic_id, topic_type, trend_use)"
                " VALUES (1, 'youtube_video:NOPE', '선크림', 'product_category', false)"
            )
        conn.rollback()


# ---------- 덮이지 않는다 ----------
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
    """활성 판본이 둘이면 두 관측이 한 모집단으로 읽힌다. 023 의 부분 유니크 인덱스가 그것을 막는다."""
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
    """빈 판본을 켜면 분석이 빈 코퍼스를 오류 없이 읽는다."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        with pytest.raises(LookupError):
            corpus.activate(cur, 9)
        conn.rollback()
        assert corpus.active_snapshot(cur) == 1


# ---------- 재현 검증 ----------
@pytest.mark.postgres
def test_the_three_reproduction_numbers_come_out_of_the_loaded_rows(seeded: str):
    _load(seeded)
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    with connect(seeded) as conn:
        assert verify.reproduce(conn) == FIXTURE_REPRODUCES
    assert manifest["reproduces"] == FIXTURE_REPRODUCES


@pytest.mark.postgres
def test_the_reproduction_reads_the_panel_through_the_active_roster(seeded: str):
    """맨 `WHERE active` 였다면 판본이 둘일 때 분모가 두 배가 되고 이 숫자는 조용히 커진다 (#31 리뷰)."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        panel.insert(cur, panel.rows(ROOT / "eval"), version=2, note="seed:test-v2")
        panel.activate(cur, 2)
        conn.commit()
    with connect(seeded) as conn:
        assert verify.reproduce(conn) == FIXTURE_REPRODUCES


# ---------- 계약과 어긋나면 거절한다 ----------
@pytest.mark.postgres
def test_a_channel_the_active_roster_gives_another_role_is_refused(seeded: str, tmp_path: Path):
    """역할은 분모를 정하는 값이라 한 표에만 산다. channel.csv 는 표가 되지 않고 대조만 된다."""
    source = _rewritten(tmp_path)
    body = (FIXTURE / "channel.csv").read_text(encoding="utf-8-sig")
    (source / "channel.csv").write_text("﻿" + body.replace(",product,", ",expert,", 1), encoding="utf-8")
    with connect(seeded) as conn, pytest.raises(corpus.CorpusMismatch, match="panel roster"):
        corpus.load(conn, source, snapshot_id=3, label="bad-roles")


def test_a_manifest_whose_rules_differ_is_refused(tmp_path: Path):
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    manifest["rules"] = list(manifest["rules"])[:-1]
    with pytest.raises(contract.ManifestMismatch, match="rules"):
        contract.check(manifest)


def test_the_fixture_manifest_carries_the_same_rules_the_archive_manifest_does():
    """픽스처가 계약과 다른 규칙을 실으면 이 스위트는 실제 코퍼스와 다른 것을 검증하게 된다."""
    contract.check(json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8")))


# ---------- 문장이 계약에 있다 ----------
@pytest.mark.parametrize("rule", contract.RULES)
def test_every_manifest_rule_is_a_sentence_in_the_formats_contract(rule: str):
    assert rule in FORMATS.read_text(encoding="utf-8")


@pytest.mark.parametrize("limitation", contract.LIMITATIONS)
def test_every_manifest_limitation_is_a_sentence_in_the_interfaces_contract(limitation: str):
    assert limitation in INTERFACES.read_text(encoding="utf-8")


def test_the_text_rule_is_a_sentence_in_the_formats_contract():
    assert contract.TEXT_RULE in FORMATS.read_text(encoding="utf-8")


def test_the_loaded_text_is_already_a_fixed_point_of_this_repos_normalizer():
    """ydc 의 normalize_text 는 한 번, cosmai 의 것은 고정점까지 돈다. 두 구현이 다르므로 text 가 다른
    뜻이 될 수 있는 자리인데, 이 코퍼스에서는 한 번으로 이미 고정점이다 -- 실측 261,317행 중 0행이
    달라진다. 픽스처가 그 성질을 잃으면(이스케이프가 남은 표본을 넣으면) 여기서 걸린다."""
    texts = [row["text"] for row in read_csv(FIXTURE / "document.csv")]
    assert texts and all(normalize_text(t) == t for t in texts)


# ---------- DDL 이 이 포크의 번호 블록에 산다 ----------
def test_the_ddl_lives_in_this_forks_number_block():
    assert re.fullmatch(r"02[0-9]_\w+\.sql", DDL.name)


def test_the_contracts_index_carries_a_row_for_this_ddl():
    assert f"ddl/needs/{DDL.name}" in README.read_text(encoding="utf-8")


@pytest.mark.postgres
@pytest.mark.parametrize("table", corpus.TABLES)
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_the_runtime_role_may_write_the_new_tables(table: str, privilege: str):
    """반입은 needs_runtime 으로 돈다 -- GRANT 가 빠지면 운영 적재만 실패한다. per-test 스키마는
    ALL TABLES 를 통째로 열어 주므로 누락이 안 보이고, 그래서 실제 배포가 만든 `needs` 를 본다."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET ROLE needs_owner"))  # migrator 는 SET ROLE 밖에서 needs 에 USAGE 가 없다
            allowed = conn.execute(
                text("select has_table_privilege('needs_runtime', :t, :p)"),
                {"t": f"needs.{table}", "p": privilege},
            ).scalar_one()
    finally:
        engine.dispose()
    assert allowed is True


@pytest.mark.postgres
def test_a_content_type_outside_the_vocabulary_is_refused(seeded: str):
    """오타 하나가 조용히 별도 계열을 열면 그 계열은 자기만의 분모를 갖는다 (022 와 같은 문장)."""
    _load(seeded)
    with connect(seeded) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE corpus_document SET content_type = 'video_longform'")
        conn.rollback()
