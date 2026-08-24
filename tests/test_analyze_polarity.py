"""`analyze polarity` 한 단계: 원천 → need_mention·wish_mention, 2회 실행이 같은 결과이고 시드와 공존한다."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql as pgsql
from sqlalchemy import create_engine

from analysis.polarity.pipeline import run
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult
from db import seed
from db.seed._common import DEFAULT_SLICES, REPO_ROOT, connect

pytestmark = pytest.mark.postgres

TUBEDEPTH_DDL = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "current" / "app.tubedepth.sql"
# 덤프 전체를 한 스키마에 부으면 trend_radar 와 alembic_version 이 부딪힌다 — 읽는 두 테이블만 세운다.
TUBEDEPTH_TABLES = ("comments", "video_snapshots")
# 운영에서 이 다섯 테이블의 SELECT 를 여는 것이 db/grants/needs_runtime_reader.sql 이다.
SOURCE_TABLES = ("review", "rank_snapshot", "product", *TUBEDEPTH_TABLES)
CAPTURED = datetime(2026, 8, 23, tzinfo=UTC)
WRITTEN = datetime(2026, 3, 4, tzinfo=UTC)
POSTED = datetime(2026, 3, 5, tzinfo=UTC)

# 시드가 이미 담고 있는 행과 같은 (src, ref, need_key, sentence) 를 만드는 원천 (dev DB 실측: slice-suncare
# 리뷰 400/400). 005 로 extractor_version 이 키에 들어간 뒤 이것은 더 이상 자연키 충돌이 아니다 — 두 행이
# 나란히 남는다. 시드 값은 need_mention·wish_mention 에서 그대로 읽었다.
SEED_NEED = ("glowpick", "146765", "7856759", "146765/7856759", "끈적유분")
SEED_NEED_SENTENCE = "엄청 끈적이고 잘 안 발리고… 돈 더주고 좋은 거 살걸 그랬어요ㅠㅠ"
SEED_NEED_AT = datetime(2026, 8, 18, tzinfo=UTC)
SEED_WISH = ("--5yicxxgp4", "UgxrFMQux3xh1gzOnI94AaABAg")
SEED_WISH_TEXT = "스킨케어 루틴 찍어주세요"
SEED_WISH_AT = datetime(2026, 4, 22, tzinfo=UTC)
SEED_COUNTS = {"need_mention": 16046, "wish_mention": 18489}  # tests/test_seed.py 의 기대값과 같은 출처

REVIEWS = [
    ("oliveyoung", "R1", "P1", 5.0, "백탁이 하나도 없어서 진짜 좋아요", WRITTEN),
    ("oliveyoung", "R2", "P1", 1.0, "백탁이 너무 심해서 최악이에요", WRITTEN),
    ("oliveyoung", "R3", "P1", 5.0, "그냥 무난합니다", WRITTEN),
    # written_at 이 NULL 인 리뷰 — captured_at 으로 폴백하고 그 수를 센다 (formats.md §시간).
    ("oliveyoung", "R4", "P1", 2.0, "끈적임이 심하고 밀려요", None),
]
COMMENTS = [
    ("V1", "C1", "쿠션형으로도 출시해주세요 제발요", 12, POSTED),
    ("V1", "C2", "항상 잘 보고 있습니다 감사합니다", 3, POSTED),
    ("V1", "C3", "저는 백탁이 너무 심해서 못 쓰겠더라고요", 5, POSTED),
]


@pytest.fixture
def sources(needs_schema: str, trend_radar_schema: str, _schema_name: str) -> Iterator[str]:
    """needs + trend_radar + tubedepth 가 한 스키마에 있다 — 운영에서는 세 스키마다 (run 의 인자)."""
    engine = create_engine(needs_schema)
    dump = TUBEDEPTH_DDL.read_text(encoding="utf-8")
    ddl = "\n".join(
        dump.split(f"CREATE TABLE tubedepth.{table} (")[1]
        .split(");")[0]
        .join((f'CREATE TABLE "{_schema_name}"."{table}" (', ");"))
        for table in TUBEDEPTH_TABLES
    )
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(ddl)
            for table in SOURCE_TABLES:
                conn.exec_driver_sql(f'GRANT SELECT ON "{_schema_name}"."{table}" TO needs_runtime')
    finally:
        engine.dispose()
    yield needs_schema


@pytest.fixture
def loaded(sources: str, needs_runtime_url: str, _schema_name: str) -> Iterator[str]:
    seed.run_all(needs_runtime_url, only=("lexicon",))
    # 원천 행은 그 스키마의 소유자로 넣는다 — needs_runtime 은 원천에 SELECT 만 갖는다 (db/grants).
    with connect(sources) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO product (source, product_key, captured_at, name, first_seen_at, last_seen_at)"
            " VALUES ('oliveyoung', 'P1', %s, '테스트 선크림 SPF50', %s, %s)",
            (CAPTURED, CAPTURED, CAPTURED),
        )
        cur.execute(
            "INSERT INTO rank_snapshot"
            " (source, board, category_key, product_key, captured_at, category_name, rank, product_name)"
            " VALUES ('oliveyoung', 'best', 'suncare', 'P1', %s, '스킨케어 > 선크림', 1, '테스트 선크림')",
            (CAPTURED,),
        )
        cur.executemany(
            "INSERT INTO review (source, review_key, captured_at, product_key, rating, body, written_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(s, k, CAPTURED, p, r, b, w) for s, k, p, r, b, w in REVIEWS],
        )
        cur.execute(
            "INSERT INTO video_snapshots (artifact_id, video_id, fetched_at, title, channel_id)"
            " VALUES ('A1', 'V1', %s, '선크림 리뷰', 'UC1')",
            (CAPTURED,),
        )
        cur.executemany(
            "INSERT INTO comments (video_id, comment_id, text, like_count, published_at,"
            " is_hearted_by_uploader, is_pinned, first_seen_at, last_seen_at)"
            " VALUES (%s, %s, %s, %s, %s, false, false, %s, %s)",
            [(v, c, t, likes, at, CAPTURED, CAPTURED) for v, c, t, likes, at in COMMENTS],
        )
        conn.commit()
    yield needs_runtime_url


def _run(url: str, schema: str, **kwargs: Any):
    with connect(url) as conn:
        return run(conn, commerce_schema=schema, youtube_schema=schema, **kwargs)


def _rows(url: str, table: str) -> list[tuple[Any, ...]]:
    query = pgsql.SQL("SELECT * FROM {} ORDER BY src, ref, mention_id").format(pgsql.Identifier(table))
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query)
        return [row[1:] for row in cur.fetchall()]  # mention_id 는 bigserial 이라 재실행마다 커진다


def test_a_second_run_leaves_exactly_the_rows_the_first_one_wrote(loaded: str, _schema_name: str):
    first = _run(loaded, _schema_name)
    need_first, wish_first = _rows(loaded, "need_mention"), _rows(loaded, "wish_mention")
    second = _run(loaded, _schema_name)
    assert (need_first, wish_first) == (_rows(loaded, "need_mention"), _rows(loaded, "wish_mention"))
    assert (second.need_rows, second.wish_rows) == (first.need_rows, first.wish_rows)
    assert first.need_rows > 0 and first.wish_rows > 0


def test_the_run_is_recorded_with_its_versions_and_the_captured_at_fallback_count(
    loaded: str, _schema_name: str
):
    found = _run(loaded, _schema_name)
    assert found.captured_at_fallbacks == 1  # REVIEWS 의 written_at NULL 한 건
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, versions, note FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
    assert row is not None
    status, versions, note = row
    assert status == "ok"
    assert versions["extractor"] == "rule-v2.2" and versions["polarity"] == "rule-v2.2"
    assert versions["lexicon"] == {"entity": 1, "aspect": 1}
    assert "captured_at_fallback=1" in note


def test_a_review_gets_the_lexicon_category_the_category_map_derives(loaded: str, _schema_name: str):
    _run(loaded, _schema_name)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT category, lexicon_category, source_product_key FROM need_mention"
            " WHERE src = 'review'"
        )
        assert cur.fetchall() == [("스킨케어 > 선크림", "선블록", "P1")]


def test_the_sunscreen_dictionary_lands_a_complaint_and_a_satisfaction_on_the_same_aspect(
    loaded: str, _schema_name: str
):
    _run(loaded, _schema_name)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ref, need_key, polarity, kind, aspect_scope, strength, rating FROM need_mention"
            " WHERE src = 'review' AND need_key = '백탁' ORDER BY ref"
        )
        found = cur.fetchall()
    assert [(r[0], r[2], r[3]) for r in found] == [
        ("P1/R1", "만족", "complaint"),
        ("P1/R2", "불만", "complaint"),
    ]
    assert {r[4] for r in found} == {"category"}
    assert (float(found[1][5]), float(found[1][6])) == (0.8, 1.0)


def test_only_the_wish_classes_the_table_accepts_become_wish_rows(loaded: str, _schema_name: str):
    _run(loaded, _schema_name)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT ref, wish_class, video_id, channel_id, like_count FROM wish_mention")
        assert cur.fetchall() == [("V1/C1", "a", "V1", "UC1", 12)]


def test_a_seed_row_survives_while_this_units_older_version_is_replaced(loaded: str, _schema_name: str):
    with connect(loaded) as conn, conn.cursor() as cur:
        for extractor, polarity in (("slice-suncare", "rule-v2.1"), ("rule-v0.9", "rule-v0.9")):
            cur.execute(
                "INSERT INTO need_mention (src, site, ref, need_key, polarity, observed_at,"
                " observed_at_resolution, month, sentence, extractor_version, polarity_version)"
                " VALUES ('review', 'oliveyoung', 'P1/OLD', '백탁', '불만', '2026-03-04', 'day',"
                " '2026-03', %s, %s, %s)",
                (f"{extractor} 문장", extractor, polarity),
            )
        conn.commit()
    _run(loaded, _schema_name)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT extractor_version FROM need_mention WHERE ref = 'P1/OLD'")
        assert cur.fetchall() == [("slice-suncare",)]


def test_since_narrows_the_run_to_the_months_that_still_matter(loaded: str, _schema_name: str):
    """폴백 행은 수집한 달에 앉는다 — since 는 그 값을 자르므로 2026-03 리뷰만 빠진다."""
    found = _run(loaded, _schema_name, since=datetime(2026, 6, 1, tzinfo=UTC).date())
    assert (found.units, found.wish_rows) == (1, 0)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ref, month FROM need_mention")
        assert cur.fetchall() == [("P1/R4", "2026-08")]


def test_scope_keeps_only_one_lexicon_category(loaded: str, _schema_name: str):
    assert _run(loaded, _schema_name, scope="샴푸").units == 0
    assert _run(loaded, _schema_name, scope="선블록").units == len(REVIEWS)


def test_a_missing_source_schema_is_a_run_with_no_rows_not_a_crash(loaded: str):
    with connect(loaded) as conn:
        found = run(conn, commerce_schema="nowhere", youtube_schema="nowhere")
    assert (found.units, found.need_rows, found.wish_rows) == (0, 0, 0)


def test_the_source_tables_are_read_as_needs_runtime(loaded: str, _schema_name: str):
    """운영에서 이 단계는 needs_runtime 으로 돈다 — 원천 SELECT 권한은 db/grants 가 준다."""
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
    assert row is not None and row[0] == "needs_runtime"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connect(loaded) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE nope (i int)")


@pytest.fixture(scope="module")
def slices() -> Path:
    """tests/test_seed.py 와 같은 탐색 — 시드 CSV 없이는 충돌을 실물로 재현할 수 없다."""
    named = os.environ.get("COSMAI_SLICES_DIR")
    found = [Path(named)] if named else [p for p in (DEFAULT_SLICES, REPO_ROOT.parents[1] / "architect")]
    for path in found:
        if path.is_dir():
            return path
    return pytest.skip("no slice-*/ next to the repo; pass COSMAI_SLICES_DIR")


@pytest.fixture
def seeded(loaded: str, sources: str, slices: Path) -> Iterator[str]:
    """시드 언급 전량 + 그 시드 행과 같은 자연키를 만드는 원천 행."""
    seed.run_all(loaded, slices=slices, only=("products", "mentions"))
    site, product_key, review_key, _, _ = SEED_NEED
    video_id, comment_id = SEED_WISH
    with connect(sources) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rank_snapshot"
            " (source, board, category_key, product_key, captured_at, category_name, rank, product_name)"
            " VALUES (%s, 'best', 'suncare', %s, %s, '선크림', 1, '시드 선크림')",
            (site, product_key, CAPTURED),
        )
        cur.execute(
            "INSERT INTO review (source, review_key, captured_at, product_key, rating, body, written_at)"
            " VALUES (%s, %s, %s, %s, 3.0, %s, %s)",
            (site, review_key, CAPTURED, product_key, SEED_NEED_SENTENCE, SEED_NEED_AT),
        )
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, like_count, published_at,"
            " is_hearted_by_uploader, is_pinned, first_seen_at, last_seen_at)"
            " VALUES (%s, %s, %s, 0, %s, false, false, %s, %s)",
            (video_id, comment_id, SEED_WISH_TEXT, SEED_WISH_AT, CAPTURED, CAPTURED),
        )
        conn.commit()
    yield loaded


def _tagged(url: str, table: str, prefix: str) -> int:
    query = pgsql.SQL("SELECT count(*) FROM {} WHERE extractor_version LIKE %s").format(
        pgsql.Identifier(table)
    )
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query, (prefix,))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def test_a_seed_row_this_run_re_derives_keeps_its_own_version(seeded: str, _schema_name: str):
    """재추출이 시드와 같은 문장·need_key 를 다시 뽑는다 — 005 로 extractor_version 이 자연키에 들어간
    뒤로 둘은 충돌하지 않고 나란히 남고, 시드 행의 버전 태그는 그대로다."""
    assert {t: _tagged(seeded, t, "slice-%") for t in SEED_COUNTS} == SEED_COUNTS
    _run(seeded, _schema_name)
    _, _, _, ref, need_key = SEED_NEED
    with connect(seeded) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT extractor_version, polarity_version FROM need_mention"
            " WHERE ref = %s AND need_key = %s AND sentence = %s ORDER BY extractor_version",
            (ref, need_key, SEED_NEED_SENTENCE),
        )
        need = cur.fetchall()
        cur.execute(
            "SELECT extractor_version, wish_class FROM wish_mention WHERE src = 'yt_comment' AND ref = %s",
            ("/".join(SEED_WISH),),
        )
        wish = cur.fetchall()
    # 시드가 살아남는 방식이 바뀌었다: UPSERT 의 WHERE 가 아니라 자연키가 행을 갈라놓는다. 이 리뷰는
    # slice-p1 도 다시 뽑은 548건 중 하나라 세 버전이 나란히 남는다(전에는 suncare 하나에 흡수됐다).
    assert need == [
        ("rule-v2.2", "rule-v2.2"),
        ("slice-p1", "rule-v2.2"),
        ("slice-suncare", "rule-v2.1"),
    ]
    assert wish == [("slice-p9", "b")]
    assert {t: _tagged(seeded, t, "slice-%") for t in SEED_COUNTS} == SEED_COUNTS


class StubPolarity:
    """등록된 구현체 자리에 꽂는 판정자 — 규칙과 다른 버전을 내는 것이 이 스텁의 요점이다."""

    version = "stub-v9"

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="백탁", polarity="중립", reason="stub", version=self.version)

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]


def test_the_implementation_the_run_was_given_is_the_version_it_records(loaded: str, _schema_name: str):
    """versioning.md: analysis_run.versions 는 그 run 의 버전을 기록한다 — 실제로 돈 구현의 것이어야 한다."""
    found = _run(loaded, _schema_name, polarity=StubPolarity())
    assert found.polarity_version == StubPolarity.version
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions, note FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
        cur.execute("SELECT DISTINCT polarity_version, polarity FROM need_mention WHERE src = 'review'")
        stamped = cur.fetchall()
    assert row is not None
    versions, note = row
    assert versions["polarity"] == StubPolarity.version and versions["extractor"] == "rule-v2.2"
    assert f"analyze:polarity:{StubPolarity.version}" in note
    assert stamped == [(StubPolarity.version, "중립")]


def test_without_an_implementation_the_rule_still_runs(loaded: str, _schema_name: str):
    assert _run(loaded, _schema_name).polarity_version == "rule-v2.2"
