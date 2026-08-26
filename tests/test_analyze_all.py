"""`cosmai analyze all`: 세 단계가 한 run 을 공유하고, 두 번째 실행이 같은 metrics 를 낸다 (#5).

원천 두 스키마는 계약 덤프 그대로 따로 세운다 — 운영에서 needs·trend_radar·tubedepth 가 세 스키마다.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from psycopg import sql as pgsql
from sqlalchemy import create_engine, text

from analysis import pipeline
from analysis.extractor import VERSION as EXTRACTOR_VERSION
from analysis.linker import LINKER_VERSION
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET
from analysis.polarity import VERSION as POLARITY_VERSION
from analysis.polarity.ownership import NO_OWNERS, OWNERS
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult
from cosmai.cli import main
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPS = REPO_ROOT / "contracts" / "ddl" / "current"
VIEW = REPO_ROOT / "db" / "views" / "analysis_health.sql"
CRONTAB_D = REPO_ROOT / "stack" / "crontab.d"
ENTRYPOINTS_MD = REPO_ROOT / "contracts" / "entrypoints.md"

CAPTURED = datetime(2026, 8, 23, tzinfo=UTC)
CAPTURED_DATE = date(2026, 8, 23)
WRITTEN = datetime(2026, 3, 4, tzinfo=UTC)
POSTED = datetime(2026, 3, 5, tzinfo=UTC)
CATEGORY = "스킨케어 > 선크림"

PRODUCTS = (
    ("oliveyoung", "A1", "라네즈 워터뱅크 선크림 SPF50 50ml", "라네즈"),
    ("glowpick", "G1", "워터뱅크 선크림 [SPF50/PA+++]", "라네즈"),
)
REVIEWS = (
    ("oliveyoung", "R1", "A1", 1.0, "백탁이 너무 심해서 최악이에요", WRITTEN),
    ("oliveyoung", "R2", "A1", 5.0, "백탁이 하나도 없어서 진짜 좋아요", WRITTEN),
    ("oliveyoung", "R3", "A1", 2.0, "끈적임이 심하고 밀려요", WRITTEN),
)
COMMENTS = (
    # 브랜드·형태가 다 들어간 바람 한 줄 — metrics_wish 는 축 값이 있는 행만 센다.
    ("V1", "C1", "라네즈 쿠션으로도 출시해주세요 제발요", 12),
    ("V1", "C2", "저는 백탁이 너무 심해서 못 쓰겠더라고요", 5),
)
NEED_METRICS = "scope, need_key, month, product_ref, neg, pos"
WISH_METRICS = "scope, format, attribute, brand, mentions"


def _apply_dump(engine: Any, dump: Path, schema: str, original: str) -> None:
    body = "\n".join(
        line
        for line in dump.read_text(encoding="utf-8").splitlines()
        if not line.startswith("\\restrict") and not line.startswith("\\unrestrict")
    )
    ddl = body.replace(f"CREATE SCHEMA {original};", "").replace(f"{original}.", f'"{schema}".')
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(ddl)
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO needs_runtime')
        conn.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO needs_runtime')


@pytest.fixture
def source_schemas(database_url_for_tests: str, _schema_name: str) -> Iterator[tuple[str, str]]:
    """두 덤프가 alembic_version 을 함께 가져서 한 스키마에 부을 수 없다 (test_linker.py 와 같은 이유)."""
    tail = hashlib.sha1(_schema_name.encode()).hexdigest()[:10]
    names = (f"trall_{tail}", f"tdall_{tail}")
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        for name in names:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    _apply_dump(engine, DUMPS / "app.trend_radar.sql", names[0], "trend_radar")
    _apply_dump(engine, DUMPS / "app.tubedepth.sql", names[1], "tubedepth")
    try:
        yield names
    finally:
        with engine.begin() as conn:
            for name in names:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        engine.dispose()


@pytest.fixture
def sources(database_url_for_tests: str, source_schemas: tuple[str, str]) -> tuple[str, str]:
    commerce, youtube = source_schemas
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        for source, key, name, brand in PRODUCTS:
            conn.exec_driver_sql(
                f'INSERT INTO "{commerce}".product '
                "(source, product_key, captured_at, name, brand, first_seen_at, last_seen_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (source, key, CAPTURED, name, brand, CAPTURED, CAPTURED),
            )
            conn.exec_driver_sql(
                f'INSERT INTO "{commerce}".rank_snapshot '
                "(source, board, category_key, product_key, captured_at, category_name, rank, "
                "product_name, price) VALUES (%s, 'best', 'suncare', %s, %s, %s, 1, %s, 12000)",
                (source, key, CAPTURED, CATEGORY, name),
            )
        for source, review_key, product_key, rating, body, written_at in REVIEWS:
            conn.exec_driver_sql(
                f'INSERT INTO "{commerce}".review '
                "(source, review_key, captured_at, product_key, rating, body, written_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (source, review_key, CAPTURED, product_key, rating, body, written_at),
            )
        conn.exec_driver_sql(
            f'INSERT INTO "{commerce}".review_stats '
            "(source, product_key, captured_at, review_count, pct_1, pct_2) "
            "VALUES ('oliveyoung', 'A1', %s, 1000, 3, 2)",
            (CAPTURED,),
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{commerce}".price_point (source, product_key, captured_at, price) '
            "VALUES ('oliveyoung', 'A1', %s, 12000)",
            (CAPTURED,),
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{youtube}".video_snapshots '
            "(artifact_id, video_id, fetched_at, title, channel_id, published_at) "
            "VALUES ('a1', 'V1', %s, '라네즈 선크림 리뷰', 'UC1', %s)",
            (CAPTURED, POSTED),
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{youtube}".transcripts '
            "(video_id, language, is_automatic, full_text, segment_count, fetched_at) "
            "VALUES ('V1', 'ko', true, '오늘은 라네즈 선크림을 발라볼게요', 3, %s)",
            (CAPTURED,),
        )
        for video_id, comment_id, text, likes in COMMENTS:
            conn.exec_driver_sql(
                f'INSERT INTO "{youtube}".comments '
                "(video_id, comment_id, text, like_count, published_at, is_hearted_by_uploader, "
                "is_pinned, first_seen_at, last_seen_at) "
                "VALUES (%s, %s, %s, %s, %s, false, false, %s, %s)",
                (video_id, comment_id, text, likes, POSTED, CAPTURED, CAPTURED),
            )
    engine.dispose()
    return commerce, youtube


@pytest.fixture
def analysis_url(needs_runtime_url: str) -> str:
    seed.run_all(needs_runtime_url, only=("lexicon",))
    return needs_runtime_url


def _all(url: str, sources: tuple[str, str], **kwargs: Any) -> pipeline.StageOutcome:
    # 이 파일의 리뷰는 전부 선크림(=선블록)이고, 배송되는 소유 표는 그 scope 를 gemma4 에 준다 (#31).
    # 여기서 검사하는 것은 세 단계의 배선이므로 주인 없는 상태로 돈다 — 소유 자체는 아래 한 테스트와
    # tests/test_analyze_polarity.py 가 본다.
    kwargs.setdefault("owners", NO_OWNERS)
    commerce, youtube = sources
    with connect(url) as conn:
        return pipeline.run_stage(
            conn,
            "all",
            commerce_schema=commerce,
            youtube_schema=youtube,
            captured_at=CAPTURED_DATE,
            **kwargs,
        )


def _dump(url: str, table: str, columns: str, run_id: int | None) -> list[tuple[Any, ...]]:
    fields = pgsql.SQL(", ").join(pgsql.Identifier(c.strip()) for c in columns.split(","))
    query = pgsql.SQL("SELECT {f} FROM {t} WHERE run_id = %s ORDER BY {f}").format(
        f=fields, t=pgsql.Identifier(table)
    )
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query, (run_id,))
        return cur.fetchall()


def test_analyze_all_runs_the_three_stages_into_one_run(analysis_url: str, sources: tuple[str, str]):
    found = _all(analysis_url, sources)
    assert found.status == "ok", found.detail
    assert found.run_id is not None
    assert found.counts["product_ref"] > 0
    assert found.counts["brand_mention"] > 0
    assert found.counts["attempted_need"] > 0
    assert found.counts["attempted_wish"] > 0
    assert found.counts["metrics_need"] > 0
    assert found.counts["metrics_wish"] > 0
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, finished_at IS NOT NULL FROM analysis_run WHERE run_id = %s", (found.run_id,)
        )
        assert cur.fetchone() == ("ok", True)
        # 세 단계가 한 run 을 나눠 쓴다 — polarity 가 연 run 에 aggregate 가 metrics 를 쓴다.
        cur.execute("SELECT count(*) FROM analysis_run")
        assert cur.fetchone() == (1,)


def test_analyze_all_writes_the_product_axis_the_product_screen_reads(
    analysis_url: str, sources: tuple[str, str]
):
    """#41: 화면 3 은 product_ref <> '' 행만 읽는다 — 집계가 그 축을 내지 않으면
    실제 run 에서 영원히 0행이다."""
    found = _all(analysis_url, sources)
    assert found.status == "ok", found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT scope, need_key, product_ref, neg, pos, unresolved FROM metrics_need "
            "WHERE run_id = %s AND month = ''",
            (found.run_id,),
        )
        rows = cur.fetchall()

    per_product = [r for r in rows if r[2]]
    sums = {(r[0], r[1]): r for r in rows if not r[2]}
    assert per_product, "제품 축 행이 하나도 없다 — 화면 3 이 빈다"
    # 화면 3 이 정렬·막대에 쓰는 값이다. 리뷰가 있는 제품 행은 unresolved 가 차 있어야 한다.
    assert any(r[5] is not None for r in per_product)
    # 카테고리 합 행은 그대로 남는다 — 제품 축은 그 위에 얹히는 것이지 대체가 아니다 (화면 1·골든).
    assert sums and {(r[0], r[1]) for r in per_product} <= set(sums)
    for scope, need_key, _, neg, pos, _ in per_product:
        # 한 제품의 몫은 그 (scope, need_key) 합을 넘지 못한다 — 제품을 모르는 언급은 합에만 남는다.
        assert neg <= sums[(scope, need_key)][3] and pos <= sums[(scope, need_key)][4]
    # 롤업은 제품마다 need_key 당 한 행이다 — 화면 3 이 그 scope 로 중복을 지운다 (screens.js).
    rolled = [(r[1], r[2]) for r in per_product if r[0] == "all"]
    assert rolled and len(set(rolled)) == len(rolled)


def test_analyze_all_leaves_the_owned_scope_to_its_owner(analysis_url: str, sources: tuple[str, str]):
    """크론이 부르는 모양 그대로다: 소유 표를 아무도 인자로 말하지 않아도 배송값(#31)이 선다. 이 픽스처의
    리뷰는 전부 선블록이라 규칙은 리뷰를 한 건도 쓰지 않고, 주인이 라벨한 행은 그 자리에 남는다."""
    commerce, youtube = sources
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO need_mention (src, site, ref, need_key, polarity, observed_at, "
            "observed_at_resolution, month, sentence, category, lexicon_category, "
            "extractor_version, polarity_version) VALUES ('review', 'oliveyoung', 'A1/R1', '백탁', "
            "'만족', '2026-03-04', 'day', '2026-03', '백탁이 너무 심해서 최악이에요', %s, '선블록', %s, %s)",
            (CATEGORY, EXTRACTOR_VERSION, OWNERS["선블록"].version),
        )
        conn.commit()
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "all", commerce_schema=commerce, youtube_schema=youtube, captured_at=CAPTURED_DATE
        )
    assert found.status == "ok", found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT lexicon_category, polarity, polarity_version FROM need_mention "
            "WHERE src = 'review'"
        )
        # 규칙이 이 문장을 다시 뽑았다면 '불만'/rule-v2.2 한 줄만 남았을 것이다 (제자리 upsert).
        assert cur.fetchall() == [("선블록", "만족", OWNERS["선블록"].version)]


def test_a_second_analyze_all_produces_the_same_metrics_row_for_row(
    analysis_url: str, sources: tuple[str, str]
):
    first = _all(analysis_url, sources)
    needs = _dump(analysis_url, "metrics_need", NEED_METRICS, first.run_id)
    wishes = _dump(analysis_url, "metrics_wish", WISH_METRICS, first.run_id)
    second = _all(analysis_url, sources)
    assert second.status == "ok" and second.run_id != first.run_id
    assert _dump(analysis_url, "metrics_need", NEED_METRICS, second.run_id) == needs
    assert _dump(analysis_url, "metrics_wish", WISH_METRICS, second.run_id) == wishes
    assert second.counts == first.counts


def test_the_run_records_every_version_and_the_active_lexicon_per_ruleset(
    analysis_url: str, sources: tuple[str, str]
):
    found = _all(analysis_url, sources)
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
    assert row is not None
    versions = row[0]
    assert versions["linker"] == LINKER_VERSION
    assert versions["extractor"] == EXTRACTOR_VERSION
    assert versions["polarity"] == POLARITY_VERSION
    assert versions["aggregate"]
    # #17 판정: lexicon 은 활성 버전 + ruleset 이다.
    assert versions["lexicon"] == {"entity": 1, "aspect": {SUNCARE_RULESET: 1, GENERIC_RULESET: 1}}


def test_only_the_version_this_run_wrote_is_aggregated(analysis_url: str, sources: tuple[str, str]):
    """시드 슬라이스를 같은 scope 에 섞으면 같은 문장이 두 번 세어진다 — 모집단을 이름으로 못 박는다."""
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO need_mention (src, site, ref, need_key, polarity, observed_at, "
            "observed_at_resolution, month, sentence, category, lexicon_category, "
            "extractor_version, polarity_version) VALUES ('review', 'oliveyoung', 'A1/S1', '백탁', "
            "'불만', '2026-03-04', 'day', '2026-03', '시드 문장', %s, '선블록', "
            "'slice-suncare', 'slice-suncare')",
            (CATEGORY,),
        )
        conn.commit()
    found = _all(analysis_url, sources)
    assert found.status == "ok", found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions->>'extractor' FROM analysis_run WHERE run_id = %s", (found.run_id,))
        assert cur.fetchone() == (EXTRACTOR_VERSION,)
        # metrics_need 의 scope 축은 원천 카테고리다 (analysis/aggregate).
        cur.execute(
            "SELECT neg, pos FROM metrics_need WHERE run_id = %s AND scope = %s AND need_key = '백탁' "
            "AND month = '' AND product_ref = ''",
            (found.run_id, CATEGORY),
        )
        row = cur.fetchone()
    # 시드 행이 모집단에 들어왔다면 neg 가 2 다 — 이 run 이 쓴 리뷰 불만 한 건만 센다.
    assert row == (1, 1)


class StubPolarity:
    """`--impl <spec>` 가 여는 판정자 자리의 스텁 — 규칙과 다른 버전을 내는 것이 요점이다
    (tests/test_analyze_polarity.py 의 같은 이름과 같은 역할)."""

    version = "stub-v9"

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="백탁", polarity="중립", reason="stub", version=self.version)

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]


def test_an_impl_run_records_that_implementations_version_not_the_rules(
    analysis_url: str, sources: tuple[str, str]
):
    """entrypoints.md: `--impl` 이 있으면 그 구현의 버전이 analysis_run.versions.polarity 와 산출 행에
    남는다. `all` 은 성공한 run 을 자기가 모은 versions 로 다시 닫으므로(analysis/pipeline.py `_close`),
    polarity 가 RUN_START 에 쓴 올바른 버전은 그 versions 가 판정자를 물어봤을 때만 살아남는다.
    `--impl` 에서 여기까지의 배선은 tests/test_cli_analyze.py 가 본다."""
    found = _all(analysis_url, sources, polarity=StubPolarity())
    assert found.status == "ok", found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
        cur.execute("SELECT DISTINCT polarity_version FROM need_mention")
        stamped = cur.fetchall()
    assert row is not None
    assert row[0]["polarity"] == StubPolarity.version
    # 나머지 버전은 그대로다 — 갈아 끼운 것은 판정자 하나뿐이다.
    assert row[0]["extractor"] == EXTRACTOR_VERSION and row[0]["linker"] == LINKER_VERSION
    assert stamped == [(StubPolarity.version,)]


def test_a_failing_stage_closes_the_run_as_failed(analysis_url: str, sources: tuple[str, str]):
    _, youtube = sources
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "all", commerce_schema="nowhere", youtube_schema=youtube, captured_at=CAPTURED_DATE
        )
    assert found.status == "failed" and found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, note FROM analysis_run ORDER BY run_id")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["failed"]
    assert "nowhere" in rows[0][1]


def test_a_polarity_failure_closes_the_run_polarity_itself_opened(
    analysis_url: str, sources: tuple[str, str], database_url_for_tests: str
):
    """polarity 는 run 을 열고 바로 커밋한다 — 그 안에서 죽으면 running 행이 고아로 남으면 안 된다.

    가장 긴 단계라 운영에서 타임아웃이 착지할 자리이기도 하다: 가장 일어나기 쉬운 실패다.
    """
    commerce, _ = sources
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        # link 는 product 만 읽는다 — review 만 닫으면 polarity 가 run 을 연 뒤에 죽는다.
        conn.exec_driver_sql(f'REVOKE SELECT ON "{commerce}".review FROM needs_runtime')
    engine.dispose()
    found = _all(analysis_url, sources)
    assert found.status == "failed" and "polarity" in found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT run_id, status, finished_at IS NOT NULL, note FROM analysis_run")
        rows = cur.fetchall()
    # 고아 running 행 + 새 failed 행이 아니라, polarity 가 연 그 행 하나가 닫힌다.
    assert [(r[1], r[2]) for r in rows] == [("failed", True)]
    assert rows[0][0] == found.run_id
    assert "polarity" in rows[0][3]


def test_each_stage_runs_on_its_own(analysis_url: str, sources: tuple[str, str]):
    commerce, youtube = sources
    with connect(analysis_url) as conn:
        link = pipeline.run_stage(conn, "link", commerce_schema=commerce, youtube_schema=youtube)
        polarity = pipeline.run_stage(
            conn, "polarity", commerce_schema=commerce, youtube_schema=youtube, owners=NO_OWNERS
        )
        aggregate = pipeline.run_stage(conn, "aggregate", commerce_schema=commerce, captured_at=CAPTURED_DATE)
    assert link.status == "ok" and link.counts["product_ref"] > 0
    assert polarity.status == "ok" and polarity.counts["attempted_need"] > 0
    assert aggregate.status == "ok" and aggregate.counts["metrics_need"] > 0


def _scopes_of(url: str, run_id: int | None) -> list[str]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT scope FROM metrics_need WHERE run_id = %s ORDER BY 1", (run_id,))
        return [r[0] for r in cur.fetchall()]


def test_analyze_all_with_a_lexicon_scope_aggregates_what_it_labelled(
    analysis_url: str, sources: tuple[str, str]
):
    """#38 택2: `--scope` 는 두 축을 다 받는다. polarity 는 lexicon_category('선블록')로 거르고
    aggregate 는 원천 카테고리('스킨케어 > 선크림')로 거른다 — 이 픽스처의 리뷰가 바로 그 어긋남 위에
    있으므로, 라벨한 것을 그 실행이 실제로 집계해야 한다 (실측 run 16 은 6시간 45분에 0행이었다)."""
    found = _all(analysis_url, sources, scope="선블록")
    assert found.status == "ok", found.detail
    assert found.counts["attempted_need"] > 0, "polarity must have actually run for this scope"
    assert found.counts["metrics_need"] > 0
    # metrics_need.scope 축은 그대로 원천 카테고리다 (contracts/entrypoints.md §분석) — 펼치는 것은
    # 어느 scope 를 쓸지이지, scope 컬럼이 무엇을 뜻하는지가 아니다. 화면(#11)·골든이 그 축을 읽는다.
    assert _scopes_of(analysis_url, found.run_id) == [CATEGORY]


def test_a_lexicon_scope_writes_the_rows_the_unscoped_run_writes_for_that_category(
    analysis_url: str, sources: tuple[str, str]
):
    """펼친 scope 의 행은 스코프 없는 05:00 실행이 그 원천 카테고리에 쓰는 행과 같아야 한다 — scope 는
    어느 카테고리를 쓸지를 고르지, 그 안에서 무엇이 세어지는지를 바꾸지 않는다."""
    whole = _all(analysis_url, sources)
    assert whole.status == "ok", whole.detail
    rows = _dump(analysis_url, "metrics_need", NEED_METRICS, whole.run_id)
    baseline = [r for r in rows if r[0] == CATEGORY]
    assert baseline, "the unscoped run must write this category, or the comparison proves nothing"
    scoped = _all(analysis_url, sources, scope="선블록")
    assert scoped.status == "ok", scoped.detail
    assert _dump(analysis_url, "metrics_need", NEED_METRICS, scoped.run_id) == baseline


def test_analyze_all_still_stops_being_quiet_when_no_source_category_carries_the_scope(
    analysis_url: str, sources: tuple[str, str], database_url_for_tests: str
):
    """#38 택3 은 남는다. 제품명 정규식(name_keyword)으로 붙은 라벨에는 원천 카테고리가 아예 없어
    (analysis/units.py) 펼칠 값이 없다 — 실측 run 16 의 '(빈 값) 56' 이 그 갈래다. 그런 실행은 라벨을
    쓰고도 한 행도 집계하지 못하므로 'ok' 로 조용히 끝나서는 안 된다."""
    commerce, _youtube = sources
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        # 카테고리를 주는 것은 rank_snapshot 뿐이다 — 지우면 남는 유도 경로는 제품명 정규식뿐이고,
        # 그 규칙은 glowpick 것만 있다 (eval/lexicon/category_map_v1.csv).
        conn.exec_driver_sql(f'DELETE FROM "{commerce}".rank_snapshot')
        conn.exec_driver_sql(
            f'INSERT INTO "{commerce}".review '
            "(source, review_key, captured_at, product_key, rating, body, written_at) "
            "VALUES ('glowpick', 'R9', %s, 'G1', 1.0, '백탁이 너무 심해서 최악이에요', %s)",
            (CAPTURED, WRITTEN),
        )
    engine.dispose()
    found = _all(analysis_url, sources, scope="선블록")
    assert found.counts["attempted_need"] > 0, "polarity must have actually run for this scope"
    assert found.counts["metrics_need"] == 0
    assert found.status == "partial", found.detail
    assert "선블록" in found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, note FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
        assert row is not None
        status, note = row
        assert status == "partial"
        assert "선블록" in note


def _insert_need(url: str, category: str | None) -> None:
    """The one shape of need_mention this file's --scope 선블록 tests all need: the label matches and
    the source category is something else (or nothing) — the #38 axes themselves, not a stand-in."""
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO need_mention (src, site, ref, need_key, polarity, observed_at, "
            "observed_at_resolution, month, sentence, category, lexicon_category, "
            "extractor_version, polarity_version) VALUES ('review', 'oliveyoung', 'A1/R1', '백탁', "
            "'만족', '2026-03-04', 'day', '2026-03', '백탁이 너무 심해서 최악이에요', %s, '선블록', %s, %s)",
            (category, EXTRACTOR_VERSION, POLARITY_VERSION),
        )
        conn.commit()


def test_analyze_aggregate_alone_takes_the_lexicon_axis_too(analysis_url: str, sources: tuple[str, str]):
    """단독 `analyze aggregate --scope <lexicon>` 도 같은 규칙으로 돈다 — 침묵 감시(#38 택3)가
    운영자에게 "aggregate 를 다시 돌려라" 라고 말하는 자리가 바로 여기다."""
    commerce, _youtube = sources
    _insert_need(analysis_url, CATEGORY)
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "aggregate", scope="선블록", commerce_schema=commerce, captured_at=CAPTURED_DATE
        )
    assert found.status == "ok", found.detail
    assert found.counts["metrics_need"] > 0
    assert _scopes_of(analysis_url, found.run_id) == [CATEGORY]


def test_a_source_category_scope_keeps_running_exactly_as_it_did(analysis_url: str, sources: tuple[str, str]):
    """회귀: 원천 카테고리 문자열을 그대로 준 실행은 그 한 scope 만 쓴다 (#38 택2 는 추가일 뿐이다)."""
    commerce, _youtube = sources
    _insert_need(analysis_url, CATEGORY)
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "aggregate", scope=CATEGORY, commerce_schema=commerce, captured_at=CAPTURED_DATE
        )
    assert found.status == "ok", found.detail
    assert found.counts["metrics_need"] > 0
    assert _scopes_of(analysis_url, found.run_id) == [CATEGORY]


def test_analyze_aggregate_alone_stops_being_quiet_when_nothing_carries_the_scope(
    analysis_url: str, sources: tuple[str, str]
):
    """standalone `analyze aggregate` closes its own run — the override has to reach that row too.
    라벨은 '선블록' 인데 원천 카테고리가 없는 행(제품명 정규식 갈래)이 그 침묵을 남긴다."""
    commerce, _youtube = sources
    _insert_need(analysis_url, None)
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "aggregate", scope="선블록", commerce_schema=commerce, captured_at=CAPTURED_DATE
        )
    assert found.counts["metrics_need"] == 0
    assert found.status == "partial", found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, note FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
        assert row is not None
        status, note = row
        assert status == "partial"
        assert "선블록" in note


def test_one_silent_scope_leaves_one_partial_row_not_two(analysis_url: str, sources: tuple[str, str]):
    """#38 의 침묵과 #16 의 stale 보고 행은 `_one` 의 aggregate 분기에서 만나고, 만나는 **순서**가
    불변식이다: `_amend_silent_scope(..., close_run=True)` 는 자기 run 행을 partial 로 닫고
    `_reported` 는 status 가 OK 가 아니면 보고 행을 하나 넣는다. 후자가 바깥이어야 한다 — 안팎이
    뒤집히면 표식 하나 없는 이 침묵 한 건에 partial 행이 둘 남고, "한 사건에 한 행"이 깨진다.

    이 자리를 지키는 것이 사람의 주의력뿐이면 다음 리베이스에서 조용히 뒤집힌다.
    """
    commerce, _youtube = sources
    _insert_need(analysis_url, None)
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "aggregate", scope="선블록", commerce_schema=commerce, captured_at=CAPTURED_DATE
        )
    assert found.status == "partial" and found.counts["metrics_need"] == 0, found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT run_id, note FROM analysis_run WHERE status = 'partial' ORDER BY run_id")
        partial_rows = cur.fetchall()
    assert [int(r[0]) for r in partial_rows] == [found.run_id], (
        f"one scope-silence must leave exactly the aggregate's own run partial, got {partial_rows}"
    )
    assert "선블록" in (partial_rows[0][1] or "")


def test_the_predicate_fires_on_need_alone_even_when_wish_is_not_empty(
    analysis_url: str, sources: tuple[str, str]
):
    """review round 1 #2: the brief's original 'both at 0' predicate was wrong. wish ignores --scope
    entirely and always recounts the whole population (WISH_SCOPES), so #33's plan to run this scope by
    scope repeatedly will keep the wish population non-empty — metrics_need alone has to carry this."""
    commerce, _youtube = sources
    _insert_need(analysis_url, None)
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO wish_mention (src, ref, video_id, observed_at, observed_at_resolution, month, "
            "wish_class, brand, format, sentence, extractor_version) VALUES ('yt_comment', 'V1/C1', "
            "'V1', '2026-03-05', 'day', '2026-03', 'a', '라네즈', '쿠션', '쿠션으로도 출시해주세요', %s)",
            (EXTRACTOR_VERSION,),
        )
        conn.commit()
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "aggregate", scope="선블록", commerce_schema=commerce, captured_at=CAPTURED_DATE
        )
    assert found.counts["metrics_wish"] > 0, "the fixture must actually exercise a non-empty wish pass"
    assert found.counts["metrics_need"] == 0
    assert found.status == "partial", found.detail
    assert "metrics_wish" not in found.detail


def test_stale_and_scope_silence_both_land_in_one_note(analysis_url: str, sources: tuple[str, str]):
    """review round 1 #3: an abandoned run's stale marker must not swallow this run's own scope-silence
    reason — both PARTIAL causes have to show up together, not whichever `_amend` ran first."""
    commerce, _youtube = sources
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO analysis_run (status, versions, note) "
            "VALUES ('failed', '{}'::jsonb, 'analyze:polarity rewriting=review/2026-03')"
        )
        conn.commit()
    _insert_need(analysis_url, None)
    with connect(analysis_url) as conn:
        found = pipeline.run_stage(
            conn, "aggregate", scope="선블록", commerce_schema=commerce, captured_at=CAPTURED_DATE
        )
    assert found.status == "partial", found.detail
    assert "half-written" in found.detail, "the stale reason must not disappear"
    assert "선블록" in found.detail, "the scope-silence reason must show too"


def test_the_cli_exits_one_when_a_stage_fails_and_two_when_it_cannot_connect(
    analysis_url: str, capsys: pytest.CaptureFixture[str]
):
    # 이 컨테이너에는 운영 뷰가 바인딩하는 것만 있다 (tool/checks/test): trend_radar 전부는 비어 있고
    # tubedepth 는 jobs 한 표뿐이다 — link 가 읽는 tubedepth 네 표는 없어서 거기서 실패한다.
    assert main(["analyze", "all", "--url", analysis_url]) == 1
    assert "failed" in capsys.readouterr().out
    # 단계에 닿기 전의 거절은 blocked 다 — 실패한 run 이 남는 exit 1 과 갈린다.
    assert main(["analyze", "all", "--url", analysis_url.replace("check-runtime", "wrong")]) == 2


def test_the_health_view_reports_the_run_and_its_metric_rows(
    analysis_url: str, sources: tuple[str, str], database_url_for_tests: str, _schema_name: str
):
    found = _all(analysis_url, sources)
    # 운영에서 이 파일을 적용하는 것은 db/migrate.sh 이고 그때의 롤이 needs_owner 다.
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(VIEW.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    engine.dispose()
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, started_at IS NOT NULL, finished_at IS NOT NULL, extractor_version, "
            "metrics_need, metrics_wish FROM analysis_health WHERE run_id = %s",
            (found.run_id,),
        )
        assert cur.fetchone() == (
            "ok", True, True, EXTRACTOR_VERSION,
            found.counts["metrics_need"], found.counts["metrics_wish"],
        )  # fmt: skip


def test_the_crontab_schedules_analyze_all_at_the_time_the_contract_names():
    def _time(text: str) -> tuple[str, ...]:
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.endswith("cosmai analyze all"):
                return tuple(line.split()[:5])
        return ()

    contract = _time(ENTRYPOINTS_MD.read_text(encoding="utf-8"))
    assert contract, "contracts/entrypoints.md §스케줄 names no time for `analyze all`"
    # The whole directory, not one file: which supercronic container carries this line is a wiring
    # decision (stack/docker-compose.yml), and it must not decide whether this check sees the line.
    scheduled = "\n".join(p.read_text(encoding="utf-8") for p in sorted(CRONTAB_D.iterdir()) if p.is_file())
    assert _time(scheduled) == contract


def test_migrate_sh_leaves_the_view_in_the_needs_schema_for_needs_runtime():
    """뷰 파일이 있어도 배포가 적용하지 않으면 운영에는 없는 것이다 — db/migrate.sh 의 (f) 단계."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        assert conn.execute(text("SELECT to_regclass('needs.analysis_health')")).scalar_one() is not None
        assert conn.execute(
            text("SELECT has_table_privilege('needs_runtime', 'needs.analysis_health', 'SELECT')")
        ).scalar_one()
    engine.dispose()  # needs_migrator 는 CONNECTION LIMIT 2 다 — 통과든 실패든 놓아준다.
