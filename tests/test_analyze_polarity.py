"""`analyze polarity` 한 단계: 원천 → need_mention·wish_mention, 2회 실행이 같은 결과이고 시드와 공존한다."""

from __future__ import annotations

import time
import urllib.error
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql as pgsql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from analysis import predictors, registry
from analysis.pipeline import run_stage
from analysis.polarity import RulePolarity
from analysis.polarity.ollama import OllamaPolarity
from analysis.polarity.ownership import ALWAYS, NO_OWNERS, OWNERS, Owner, unready
from analysis.polarity.pipeline import run
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult
from db import seed
from db.seed._common import connect

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

# P1 은 선블록(suncare-v2.2 사전), P2 는 샴푸(p1-v2.2 사전) — 스코프 없는 실행의 기본 모양이다.
# 한 달의 한 페이지가 두 사전을 함께 싣고, need_rows 가 그 둘을 나눠 부른 뒤 되돌린다.
REVIEWS = [
    ("oliveyoung", "R1", "P1", 5.0, "백탁이 하나도 없어서 진짜 좋아요", WRITTEN),
    ("oliveyoung", "R2", "P1", 1.0, "백탁이 너무 심해서 최악이에요", WRITTEN),
    ("oliveyoung", "R3", "P1", 5.0, "그냥 무난합니다", WRITTEN),
    # written_at 이 NULL 인 리뷰 — captured_at 으로 폴백하고 그 수를 센다 (formats.md §시간).
    ("oliveyoung", "R4", "P1", 2.0, "끈적임이 심하고 밀려요", None),
    ("oliveyoung", "R5", "P2", 1.0, "비듬이 너무 심해서 최악이에요", WRITTEN),
]
SUNCARE_REVIEWS = 4  # 위 다섯 중 P1 의 것
SHAMPOO_REVIEWS = 1
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
        cur.executemany(
            "INSERT INTO product (source, product_key, captured_at, name, first_seen_at, last_seen_at)"
            " VALUES ('oliveyoung', %s, %s, %s, %s, %s)",
            [
                ("P1", CAPTURED, "테스트 선크림 SPF50", CAPTURED, CAPTURED),
                ("P2", CAPTURED, "테스트 샴푸 500ml", CAPTURED, CAPTURED),
            ],
        )
        cur.executemany(
            "INSERT INTO rank_snapshot"
            " (source, board, category_key, product_key, captured_at, category_name, rank, product_name)"
            " VALUES ('oliveyoung', 'best', %s, %s, %s, %s, 1, %s)",
            [
                ("suncare", "P1", CAPTURED, "스킨케어 > 선크림", "테스트 선크림"),
                ("haircare", "P2", CAPTURED, "헤어케어 > 샴푸", "테스트 샴푸"),
            ],
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
    """소유 표를 말하지 않은 실행은 주인이 없는 상태로 돈다 — 소유 이전(#31)의 동작이 그것이다."""
    kwargs.setdefault("owners", NO_OWNERS)
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
    assert versions["extractor"] == "rule-v2.3" and versions["polarity"] == "rule-v2.2"
    assert versions["lexicon"] == {"entity": 1, "aspect": 1}
    assert "captured_at_fallback=1" in note


def test_a_review_gets_the_lexicon_category_the_category_map_derives(loaded: str, _schema_name: str):
    _run(loaded, _schema_name)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT category, lexicon_category, source_product_key FROM need_mention"
            " WHERE src = 'review' ORDER BY source_product_key"
        )
        assert cur.fetchall() == [
            ("스킨케어 > 선크림", "선블록", "P1"),
            ("헤어케어 > 샴푸", "샴푸", "P2"),
        ]


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
    assert _run(loaded, _schema_name, scope="샴푸").units == SHAMPOO_REVIEWS
    assert _run(loaded, _schema_name, scope="선블록").units == SUNCARE_REVIEWS


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


@pytest.fixture
def seeded(loaded: str, sources: str) -> Iterator[str]:
    """시드 언급 전량 + 그 시드 행과 같은 자연키를 만드는 원천 행."""
    seed.run_all(loaded, only=("products", "mentions"))
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
        ("rule-v2.3", "rule-v2.2"),
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
    assert versions["polarity"] == StubPolarity.version and versions["extractor"] == "rule-v2.3"
    assert f"analyze:polarity:{StubPolarity.version}" in note
    assert stamped == [(StubPolarity.version, "중립")]


def test_without_an_implementation_the_rule_still_runs(loaded: str, _schema_name: str):
    assert _run(loaded, _schema_name).polarity_version == "rule-v2.2"


# 이전 실행이 남긴 행: --scope 가 다시 쓰지 않을 자리들이다 (다른 카테고리 · 다른 src).
OTHER_SCOPE = ("review", "P9/R9", "샴푸", "백탁")
OTHER_SRC = ("yt_comment", "V9/C9", None, "백탁")
# 이 스코프가 다시 쓰는 자리에 남은 옛 행. need_key 가 달라 upsert 가 제자리에서 덮지 못한다 — 자연키에
# polarity_version 이 없으므로 옛 판정을 치우는 것은 삭제뿐이다.
SAME_SCOPE = ("review", "P8/R8", "선블록", "끈적유분")
STALE_MONTH = "2026-03"


@pytest.fixture
def with_other_scopes(loaded: str) -> str:
    with connect(loaded) as conn, conn.cursor() as cur:
        for src, ref, lexicon_category, need_key in (OTHER_SCOPE, OTHER_SRC, SAME_SCOPE):
            cur.execute(
                "INSERT INTO need_mention (src, site, ref, lexicon_category, need_key, polarity,"
                " observed_at, observed_at_resolution, month, sentence, extractor_version,"
                " polarity_version) VALUES (%s, 'oliveyoung', %s, %s, %s, '불만', '2026-03-04',"
                " 'day', %s, '이전 실행이 남긴 문장', 'rule-v2.2', 'rule-v2.2')",
                (src, ref, lexicon_category, need_key, STALE_MONTH),
            )
        cur.execute(
            "INSERT INTO wish_mention (src, ref, video_id, observed_at, observed_at_resolution, month,"
            " wish_class, sentence, extractor_version)"
            " VALUES ('yt_comment', 'V9/C9', 'V9', '2026-03-05', 'day', %s, 'a', '쿠션형 내주세요',"
            " 'rule-v2.2')",
            (STALE_MONTH,),
        )
        conn.commit()
    return loaded


def _refs(url: str, table: str) -> list[str]:
    query = pgsql.SQL("SELECT ref FROM {} WHERE ref IN ('P9/R9', 'V9/C9') ORDER BY ref").format(
        pgsql.Identifier(table)
    )
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query)
        return [row[0] for row in cur.fetchall()]


def test_a_scoped_run_does_not_delete_the_rows_it_will_not_rewrite(with_other_scopes: str, _schema_name: str):
    """스코프 밖 행은 이 실행이 다시 쓰지 않는다 — 지우면 그 달에서 사라진다 (재라벨이면 매번)."""
    before = _refs(with_other_scopes, "need_mention")
    assert before == ["P9/R9", "V9/C9"]
    _run(with_other_scopes, _schema_name, scope="선블록", polarity=StubPolarity())
    assert _refs(with_other_scopes, "need_mention") == before


def test_a_scoped_run_does_not_delete_wish_rows_it_will_not_rewrite(
    with_other_scopes: str, _schema_name: str
):
    """--scope 는 lexicon_category 로 자르는데 wish_mention 에는 그 열이 없다 — 스코프 실행은
    wish 행을 하나도 만들지 않으므로 하나도 지워서는 안 된다."""
    _run(with_other_scopes, _schema_name, scope="선블록", polarity=StubPolarity())
    assert _refs(with_other_scopes, "wish_mention") == ["V9/C9"]


def _stale(url: str, ref: str) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT need_key, lexicon_category FROM need_mention WHERE ref = %s ORDER BY need_key", (ref,)
        )
        return cur.fetchall()


def test_a_scoped_run_deletes_its_own_scopes_stale_rows(with_other_scopes: str, _schema_name: str):
    """자연키에 polarity_version 이 없어 같은 need_key 는 제자리 upsert 지만, 새 판정자가 다른 aspect 를
    내면 옛 need_key 행은 그대로 남는다 — 그러면 aggregate 가 한 문장을 두 번 센다. 스코프를 좁힌
    그 삭제가 막는 것이 이것이다."""
    _, ref, lexicon_category, need_key = SAME_SCOPE
    assert _stale(with_other_scopes, ref) == [(need_key, lexicon_category)]
    _run(with_other_scopes, _schema_name, scope="선블록", polarity=StubPolarity())
    assert _stale(with_other_scopes, ref) == []


def test_an_unscoped_rerun_still_replaces_this_units_own_stale_rows(
    with_other_scopes: str, _schema_name: str
):
    """스코프가 없으면 전량을 다시 쓴다 — 그때는 옛 버전 행을 치우는 것이 여전히 이 단계의 몫이다."""
    _run(with_other_scopes, _schema_name, polarity=StubPolarity())
    assert _refs(with_other_scopes, "need_mention") == []


# needs_runtime 의 두 한도(db/bootstrap.sql: 60s · 15s)를 몇 초 안에 넘기도록 압축한 것.
# tests/test_ollama_predictor_connection.py 와 같은 관용구다.
SQUEEZED_TIMEOUTS = "-c transaction_timeout=400ms -c idle_in_transaction_session_timeout=200ms"
EFFECTIVE_TIMEOUTS = (
    "SELECT current_setting('transaction_timeout'), current_setting('idle_in_transaction_session_timeout')"
)
SLOW_CALL_S = 0.5  # 압축한 두 한도보다 한참 길다 — 왕복을 트랜잭션 안에서 기다리면 여기서 죽는다
OLLAMA_ANSWER = '{"aspect": "백탁", "polarity": "불만", "reason": "stub"}'


def _squeezed(base_url: str) -> str:
    url = make_url(base_url)
    existing = url.query.get("options", "")
    return url.update_query_dict({"options": f"{existing} {SQUEEZED_TIMEOUTS}".strip()}).render_as_string(
        hide_password=False
    )


def test_a_slow_classifier_never_waits_for_its_answer_inside_a_transaction(
    loaded: str, _schema_name: str, monkeypatch: pytest.MonkeyPatch
):
    """ollama 는 문장마다 수백 ms~수 초를 기다린다(analysis/polarity/ollama.py). 그 기다림이 열린
    트랜잭션 안에 있으면 단계의 커넥션도 판정자의 원장 커넥션도 첫 페이지에서 끊긴다 — 압축한 한도로
    그것을 몇 초 안에 재현한다. ollama·GPU 는 필요 없다: 왕복만 스텁이다.
    """
    squeezed = _squeezed(loaded)
    # 압축이 실제로 먹었는지 먼저 확인한다 — 안 그러면 아래 단언이 공짜로 통과한다.
    with connect(squeezed) as probe, probe.cursor() as cur:
        cur.execute(EFFECTIVE_TIMEOUTS)
        assert cur.fetchone() == ("400ms", "200ms")

    calls = 0

    def slow_post(self: OllamaPolarity, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        time.sleep(SLOW_CALL_S)
        return {"message": {"content": OLLAMA_ANSWER}, "prompt_eval_count": 7, "eval_count": 3}

    monkeypatch.setattr(OllamaPolarity, "_post", slow_post)
    monkeypatch.setattr(predictors, "LEXICON_URL", squeezed)  # 원장 커넥션도 압축한 곳으로 보낸다
    registry.load_implementations()

    with registry.open_classifier("polarity", "ollama:gemma4:latest") as polarity:
        found = _run(squeezed, _schema_name, polarity=polarity)
    # 판정에 쓴 시간이 압축한 한도를 크게 넘었어야 재현이다 (넘지 않으면 통과가 무의미하다).
    assert calls * SLOW_CALL_S > 1.0
    assert found.need_rows == calls and found.polarity_version.startswith("llm-ollama-gemma4")


UNREACHABLE = "ollama 가 응답하지 않는다"


def test_an_unreachable_ollama_closes_the_run_instead_of_leaving_it_running(
    loaded: str, _schema_name: str, monkeypatch: pytest.MonkeyPatch
):
    """왕복 실패(URLError·TimeoutError)는 OSError 라 analysis/pipeline.py 의 FAILURES 밖이다 — 감싸지
    않으면 단계가 트레이스백으로 끝나고 polarity 가 연 run 이 'running' 인 채 영원히 열린 채로 남는다
    (analysis_health 가 그 run 을 도는 중이라고 계속 보고한다). 유료 경로는 _Blocking 이 그 자리를 막는다.
    """

    def refuse(self: OllamaPolarity, payload: dict[str, Any]) -> dict[str, Any]:
        raise urllib.error.URLError(UNREACHABLE)

    monkeypatch.setattr(OllamaPolarity, "_post", refuse)
    monkeypatch.setattr(predictors, "LEXICON_URL", loaded)
    registry.load_implementations()
    with registry.open_classifier("polarity", "ollama:gemma4:latest") as polarity, connect(loaded) as conn:
        found = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, polarity=polarity
        )
    assert found.status == "failed" and UNREACHABLE in found.detail
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, finished_at IS NOT NULL FROM analysis_run")
        assert cur.fetchall() == [("failed", True)]


def test_two_dictionaries_on_one_page_land_on_their_own_sentences(loaded: str, _schema_name: str):
    """스코프 없는 실행의 한 달 한 페이지에는 선블록(suncare-v2.2)과 샴푸(p1-v2.2)가 섞여 들어온다.
    need_rows 는 사전별로 묶어 classify_many 를 부르고 그 결과를 *전역* 인덱스로 되돌린다 — 그룹-로컬
    인덱스로 되돌리면 뒤 그룹의 판정이 앞 그룹의 문장에 붙고 뒤 그룹은 행을 통째로 잃는다.
    '비듬'은 p1-v2.2 의 트러블에만 있다(suncare-v2.2 의 트러블 패턴에는 없다) — 이 행이 있다는 것이
    그 문장을 generic 사전이 봤다는 증거다.
    """
    _run(loaded, _schema_name)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ref, lexicon_category, need_key, polarity, sentence FROM need_mention"
            " WHERE src = 'review' ORDER BY ref, need_key"
        )
        found = cur.fetchall()
    assert found == [
        ("P1/R1", "선블록", "백탁", "만족", "백탁이 하나도 없어서 진짜 좋아요"),
        ("P1/R2", "선블록", "백탁", "불만", "백탁이 너무 심해서 최악이에요"),
        ("P1/R4", "선블록", "끈적유분", "불만", "끈적임이 심하고 밀려요"),
        ("P2/R5", "샴푸", "트러블", "불만", "비듬이 너무 심해서 최악이에요"),
    ]


# 구현 소유권 (#31): 선블록은 gemma4 가, 나머지는 규칙이 갱신한다 — 표는 ownership.py 한 곳이다.
GEMMA4 = OWNERS["선블록"].version
# 규칙 실행이 다시 뽑지 않는 자리에 남은 주인의 행 — 삭제문이 이것을 지우는지 본다.
OWNED_ONLY = ("P1/R7", "끈적유분", "gemma4 만 본 문장")
# 규칙 실행이 같은 자연키로 다시 쓰는 자리 — 005 의 자연키에 polarity_version 이 없어 제자리 upsert 가
# 주인의 라벨을 덮을 수 있다. 규칙은 이 문장을 '불만'으로 읽는다 (test_two_dictionaries... 참고).
CONTESTED = ("P1/R2", "백탁", "백탁이 너무 심해서 최악이에요")


def _label(url: str, ref: str, need_key: str, sentence: str, version: str, polarity: str = "만족") -> None:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO need_mention (src, site, ref, lexicon_category, need_key, polarity,"
            " observed_at, observed_at_resolution, month, sentence, extractor_version,"
            " polarity_version) VALUES ('review', 'oliveyoung', %s, '선블록', %s, %s, '2026-03-04',"
            " 'day', '2026-03', %s, 'rule-v2.3', %s)",
            (ref, need_key, polarity, sentence, version),
        )
        conn.commit()


def _labels(url: str, ref: str) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT need_key, polarity, polarity_version FROM need_mention WHERE ref = %s"
            " ORDER BY need_key, polarity_version",
            (ref,),
        )
        return cur.fetchall()


def test_an_unscoped_rule_run_does_not_delete_the_owners_rows(loaded: str, _schema_name: str):
    """오늘의 결함 그대로다: 스코프 없는 규칙 실행이 매일 05:00 에 gemma4 라벨을 통째로 지웠다.
    소유 표가 배송되는 값 그대로(=선블록은 gemma4)일 때 그 행은 그 자리에 남아야 한다."""
    ref, need_key, sentence = OWNED_ONLY
    _label(loaded, ref, need_key, sentence, GEMMA4)
    with connect(loaded) as conn:  # 크론이 부르는 모양: 소유 표를 말하지 않으면 배송값이 선다
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name)
    assert _labels(loaded, ref) == [(need_key, "만족", GEMMA4)]


def test_an_unscoped_rule_run_does_not_overwrite_the_owners_label(loaded: str, _schema_name: str):
    """같은 문장을 규칙이 다시 뽑는 자리 — 자연키에 polarity_version 이 없어 삭제를 피해도 제자리
    upsert 가 주인의 라벨을 규칙 라벨로 갈아 끼운다. 주인 아닌 실행은 그 문장을 아예 판정하지 않는다."""
    ref, need_key, sentence = CONTESTED
    _label(loaded, ref, need_key, sentence, GEMMA4)
    with connect(loaded) as conn:
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name)
    assert _labels(loaded, ref) == [(need_key, "만족", GEMMA4)]


class OwnerPolarity:
    """선블록의 주인 자리에 꽂는 스텁 — 규칙과도 경쟁자와도 다른 버전을 내는 것이 요점이다."""

    version = "stub-owner-v9"

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="백탁", polarity="만족", reason="owner", version=self.version)

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]


class RivalPolarity(OwnerPolarity):
    """크론의 자리 — 스코프 없이 전량을 돈다. 주인의 문장까지 가져가면 그 라벨이 사라진다."""

    version = "stub-rival-v9"

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="백탁", polarity="불만", reason="rival", version=self.version)


def _by_scope(url: str) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT lexicon_category, polarity, polarity_version FROM need_mention"
            " WHERE src = 'review' GROUP BY 1, 2, 3 ORDER BY 1, 3"
        )
        return cur.fetchall()


def test_the_owner_keeps_the_scope_a_later_unscoped_run_walks_over(loaded: str, _schema_name: str):
    """두 구현이 같은 문장을 두고 다툰다: 주인이 먼저 선블록을 라벨하고, 그 뒤 스코프 없는 실행이 전량을
    돈다. 주인의 scope 는 그대로, 나머지(샴푸)는 나중 실행의 것이다."""
    owners = {"선블록": Owner(OwnerPolarity.version, ALWAYS)}
    _run(loaded, _schema_name, scope="선블록", polarity=OwnerPolarity(), owners=owners)
    _run(loaded, _schema_name, polarity=RivalPolarity(), owners=owners)
    assert _by_scope(loaded) == [
        ("샴푸", "불만", RivalPolarity.version),
        ("선블록", "만족", OwnerPolarity.version),
    ]


def test_with_no_owners_the_later_run_takes_every_scope_as_it_always_did(loaded: str, _schema_name: str):
    """회귀 방지: 소유 표가 비면 오늘 동작 그대로다 — 나중 실행이 전량을 가져간다."""
    _run(loaded, _schema_name, scope="선블록", polarity=OwnerPolarity(), owners=NO_OWNERS)
    _run(loaded, _schema_name, polarity=RivalPolarity(), owners=NO_OWNERS)
    assert _by_scope(loaded) == [
        ("샴푸", "불만", RivalPolarity.version),
        ("선블록", "불만", RivalPolarity.version),
    ]


def test_a_run_that_names_a_scope_it_does_not_own_is_refused(loaded: str, _schema_name: str):
    """`--scope 선블록` 을 --impl 없이 부르면 규칙이 주인의 자리를 도는 셈이다. 조용한 무동작이 아니라
    거절이어야 운영자가 표를 본다."""
    with pytest.raises(ValueError, match=GEMMA4):
        _run(loaded, _schema_name, scope="선블록", owners=OWNERS)


def test_the_refusal_closes_the_stage_as_failed_instead_of_writing_nothing_quietly(
    loaded: str, _schema_name: str
):
    """entrypoints.md §분석 이 약속하는 모양: 거절은 `analysis_run.status='failed'` 로 남고 CLI 는 1 을
    낸다 — 열린 채 남는 run 도, 아무 일 없었다는 듯한 종료 코드 0 도 아니다."""
    with connect(loaded) as conn:
        found = run_stage(
            conn, "polarity", scope="선블록", commerce_schema=_schema_name, youtube_schema=_schema_name
        )
    assert found.status == "failed" and GEMMA4 in found.detail
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, finished_at IS NOT NULL FROM analysis_run")
        assert cur.fetchall() == [("failed", True)]


def test_the_owner_table_names_the_version_the_implementation_actually_stamps():
    """소유가 바뀌면(구현 교체 · few-shot/프롬프트 판본 상승) 이 단언이 먼저 깨진다 — 표만 옮기고
    산출 행의 버전이 따라오지 않으면 주인 없는 scope 가 조용히 생긴다."""
    assert OWNERS["선블록"].version == OllamaPolarity().version


def test_every_registered_scope_names_the_same_owner_version():
    """오타로 한 줄만 다른 문자열이 되면 그 카테고리는 조용히 무주공산이 된다 (#31) — 등록된 1개가
    가리키는 값이 하나인지를 표 자체로 확인한다."""
    assert len(OWNERS) == 1
    assert {o.version for o in OWNERS.values()} == {OllamaPolarity().version}


# 저장된 lexicon_category 와 오늘의 매핑이 갈리는 자리 — rank_snapshot 의 최신 행과 category_map 이 매일
# 다시 계산하니 한 제품의 카테고리는 움직인다. 그때 주인의 행은 옛 scope 에 남고, 규칙은 같은 문장을 새
# scope 로 다시 뽑는다. P2/R5 는 오늘 '샴푸'로, 규칙은 그 문장에서 '트러블'을 낸다.
MOVED = ("P2/R5", "비듬이 너무 심해서 최악이에요")
RULE_KEY = "트러블"


def test_an_unscoped_rule_run_does_not_overwrite_an_owned_row_whose_scope_moved(
    loaded: str, _schema_name: str
):
    """두 구현이 같은 need_key 를 고르면 자연키(005)가 통째로 겹친다 — 삭제를 피한 주인의 행을 제자리
    upsert 가 갈아 끼운다. 삭제문에 있는 소유 술어가 갱신문에도 있어야 한다."""
    ref, sentence = MOVED
    _label(loaded, ref, RULE_KEY, sentence, GEMMA4)
    with connect(loaded) as conn:  # 크론이 부르는 모양 그대로: 배송 표가 선다
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name)
    assert _labels(loaded, ref) == [(RULE_KEY, "만족", GEMMA4)]


def test_a_sentence_whose_scope_moved_keeps_the_owners_label_beside_the_new_scopes(
    loaded: str, _schema_name: str
):
    """need_key 가 갈리면 두 행이 나란히 남는다 — entrypoints.md §분석 이 '한 문장에 라벨 하나'를
    어디까지 약속할 수 있는지가 여기서 정해진다. 옛 scope 의 행은 주인의 판본이 오를 때 치워진다.
    이 시나리오는 '새 scope 가 아직 주인이 없을 때'를 보이는 것이라, 전역 OWNERS 를 그대로 써도 되는
    지금도 owners={"선블록": GEMMA4} 로 표를 좁혀 그 모양을 그대로 지킨다(표 크기와 무관한 형태)."""
    ref, sentence = MOVED
    _label(loaded, ref, "백탁", sentence, GEMMA4)
    with connect(loaded) as conn:
        run(
            conn,
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            owners={"선블록": Owner(GEMMA4, ALWAYS)},
        )
    assert _labels(loaded, ref) == [("백탁", "만족", GEMMA4), (RULE_KEY, "불만", "rule-v2.2")]


class DriftedPolarity(OwnerPolarity):
    """두 번째 실행의 판정자 — 판본도 aspect 도 앞 실행과 다르다. aspect 가 같으면 옛 행이 제자리
    upsert 로 갱신돼 삭제문이 실제로 그 행을 잡는지 볼 수 없다 (자연키에 need_key 가 있다)."""

    version = "stub-drifted-v9"

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="끈적유분", polarity="불만", reason="drifted", version=self.version)


def _comment_versions(url: str) -> list[str]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT polarity_version FROM need_mention WHERE src = 'yt_comment' ORDER BY 1")
        return [row[0] for row in cur.fetchall()]


def test_a_rerun_with_a_new_version_clears_the_rows_that_have_no_lexicon_category(
    loaded: str, _schema_name: str
):
    """댓글 행에는 lexicon_category 가 없다. 배송 표가 서면 삭제문의 소유 술어가 `NULL <> ALL(...)` 을
    묻게 되는데 그 값은 NULL 이라, IS NULL 갈래가 빠지면 옛 판본 행이 어떤 재실행으로도 사라지지 않는다."""
    _run(loaded, _schema_name, polarity=StubPolarity(), owners=OWNERS)
    assert _comment_versions(loaded) == [StubPolarity.version]
    _run(loaded, _schema_name, polarity=DriftedPolarity(), owners=OWNERS)
    assert _comment_versions(loaded) == [DriftedPolarity.version]


def test_only_the_rule_may_be_let_loose_without_a_scope():
    """`--impl` 을 풀어줄지 마는지의 기준은 유료 여부가 아니라 '규칙이 아닌 구현'이다: 전량이 기본인
    것은 05:00 의 규칙 하나뿐이고, 나머지는 시간이든 돈이든 자기 자리에서만 쓴다 (cosmai/cli.py)."""
    assert unready(OWNERS, RulePolarity.version, None) is None
    assert "--scope" in str(unready(OWNERS, GEMMA4, None))
    # 아직 주인이 없는 카테고리(OWNERS 에 없는 이름) — 안 막으면 성공하고도 다음 05:00 에 지워진다.
    assert "ownership.py" in str(unready(OWNERS, GEMMA4, "미등록카테고리"))
    assert unready(OWNERS, GEMMA4, "선블록") is None
    # 남의 scope 는 이 함수의 일이 아니다: 단계가 failed run 으로 거절한다 (entrypoints.md §분석).
    assert unready(OWNERS, "stub-v9", "선블록") is None
