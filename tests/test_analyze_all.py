"""`cosmai analyze all`: the three stages share one run and a second execution emits the same metrics (#5).

The two source schemas are stood up separately from the contract dumps -- in production needs, trend_radar
and tubedepth are three schemas.
"""

from __future__ import annotations

import hashlib
import os
import re
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
    # One wish line holding both brand and format -- metrics_wish counts only rows with a value on the axis.
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
    """The two dumps both carry alembic_version and cannot be poured into one schema (the same reason as
    test_linker.py)."""
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
        # The three stages share one run -- aggregate writes metrics into the run polarity opened.
        cur.execute("SELECT count(*) FROM analysis_run")
        assert cur.fetchone() == (1,)


def test_analyze_all_writes_the_product_axis_the_product_screen_reads(
    analysis_url: str, sources: tuple[str, str]
):
    """#41: screen 3 reads only rows with product_ref <> '' -- if the aggregation does not emit that axis it
    is 0 rows forever on a real run."""
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
    # The value screen 3 uses for sorting and bars. A product row with reviews has to have unresolved
    # filled.
    assert any(r[5] is not None for r in per_product)
    # The category total row stays -- the product axis is laid on top of it, not a replacement (screen 1 and
    # the golden set).
    assert sums and {(r[0], r[1]) for r in per_product} <= set(sums)
    for scope, need_key, _, neg, pos, _ in per_product:
        # One product's share cannot exceed the (scope, need_key) total -- a mention with no known product
        # stays only in the total.
        assert neg <= sums[(scope, need_key)][3] and pos <= sums[(scope, need_key)][4]
    # The rollup is one row per need_key per product -- screen 3 dedupes by that scope (screens.js).
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
    # Decision of #17: lexicon is the active version + ruleset.
    assert versions["lexicon"] == {"entity": 1, "aspect": {SUNCARE_RULESET: 1, GENERIC_RULESET: 1}}


def test_only_the_version_this_run_wrote_is_aggregated(analysis_url: str, sources: tuple[str, str]):
    """Mixing a seed slice into the same scope counts the same sentence twice -- the population is pinned down
    by name."""
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
        # The scope axis of metrics_need is the source category (analysis/aggregate).
        cur.execute(
            "SELECT neg, pos FROM metrics_need WHERE run_id = %s AND scope = %s AND need_key = '백탁' "
            "AND month = '' AND product_ref = ''",
            (found.run_id, CATEGORY),
        )
        row = cur.fetchone()
    # Had a seed row entered the population, neg would be 2 -- only the one review complaint this run wrote
    # is counted.
    assert row == (1, 1)


class StubPolarity:
    """The stub in the classifier slot `--impl <spec>` opens -- emitting a version other than the rules' is
    the point (the same name and the same role as in tests/test_analyze_polarity.py)."""

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
    """entrypoints.md: with `--impl`, the version of that implementation stays in
    analysis_run.versions.polarity and on the output rows. `all` closes a successful run again with the
    versions it gathered (`_close` in analysis/pipeline.py), so the correct version polarity wrote in
    RUN_START survives only when those versions asked the classifier.
    The wiring from `--impl` to here is checked by tests/test_cli_analyze.py."""
    found = _all(analysis_url, sources, polarity=StubPolarity())
    assert found.status == "ok", found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions FROM analysis_run WHERE run_id = %s", (found.run_id,))
        row = cur.fetchone()
        cur.execute("SELECT DISTINCT polarity_version FROM need_mention")
        stamped = cur.fetchall()
    assert row is not None
    assert row[0]["polarity"] == StubPolarity.version
    # The other versions are unchanged -- only the classifier was swapped.
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
    """polarity opens the run and commits at once -- dying inside it must not leave an orphan running row.

    It is also the longest stage and where a timeout lands in production: the most likely failure.
    """
    commerce, _ = sources
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        # link reads product only -- closing review alone makes polarity die after it opened the run.
        conn.exec_driver_sql(f'REVOKE SELECT ON "{commerce}".review FROM needs_runtime')
    engine.dispose()
    found = _all(analysis_url, sources)
    assert found.status == "failed" and "polarity" in found.detail
    with connect(analysis_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT run_id, status, finished_at IS NOT NULL, note FROM analysis_run")
        rows = cur.fetchall()
    # Not an orphan running row plus a new failed row: the one row polarity opened is closed.
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
    """A row of an expanded scope has to equal the row a scope-less 05:00 run writes to that source category
    -- scope picks which category to write under and does not change what is counted inside it."""
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
        # Only rank_snapshot gives a category -- delete it and the one derivation path left is the
        # product-name regex, and that rule exists for glowpick alone
        # (eval/lexicon/category_map_v1.csv).
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
    """Regression: a run given the source category string as it is writes that one scope alone (option 2 of
    #38 is only an addition)."""
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
    """The silence of #38 and the stale reporting row of #16 meet in the aggregate branch of `_one`, and the
    **order** in which they meet is the invariant: `_amend_silent_scope(..., close_run=True)` closes its own
    run row as partial and `_reported` inserts one reporting row when the status is not OK. The latter has to
    be the outer one -- inverted, this one silence with not a single mark leaves two partial rows and "one row
    per event" breaks.

    If human attention is the only thing keeping this place, the next rebase turns it over quietly.
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
    # The harness container holds all three schemas and no rows (db/migrate.sh step (0) builds the two
    # source schemas whole since #178), so link now runs and finds nothing and it is aggregate that
    # refuses -- "no mentions with extractor_version". The exit code this test is about is the same;
    # the stage that produces it is not the one that did while tubedepth was three tables.
    assert main(["analyze", "all", "--url", analysis_url]) == 1
    assert "failed" in capsys.readouterr().out
    # A refusal before reaching a stage is blocked -- apart from the exit 1 that leaves a failed run.
    assert main(["analyze", "all", "--url", analysis_url.replace("check-runtime", "wrong")]) == 2


def test_the_health_view_reports_the_run_and_its_metric_rows(
    analysis_url: str, sources: tuple[str, str], database_url_for_tests: str, _schema_name: str
):
    found = _all(analysis_url, sources)
    # In production this file is applied by db/migrate.sh, and the role then is needs_owner.
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


ANALYZE_LINE = re.compile(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(cosmai analyze .+)$")


def test_the_contract_and_the_crontab_agree_on_every_analyze_line():
    """`analyze all` 하나만 대조하던 검사를 넓힌다 (#32): gemma4 줄이 크론에만 있고 계약에 없으면
    §스케줄 은 "매일 밤 무엇이 도는가"에 거짓을 답한다 — 간격 규칙이 서는 표가 바로 그 표다."""

    def _times(text: str) -> dict[str, str]:
        found = {}
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if match := ANALYZE_LINE.match(line):
                found[" ".join(match.group(2).split())] = " ".join(match.group(1).split())
        return found

    contract = _times(ENTRYPOINTS_MD.read_text(encoding="utf-8"))
    scheduled = _times(
        "\n".join(p.read_text(encoding="utf-8") for p in sorted(CRONTAB_D.iterdir()) if p.is_file())
    )
    assert scheduled, "stack/crontab.d 에 analyze 줄이 하나도 없다"
    assert scheduled == contract, f"크론 {scheduled} vs 계약 {contract}"


def test_migrate_sh_leaves_the_view_in_the_needs_schema_for_needs_runtime():
    """A view file that the deployment does not apply is absent in production -- step (f) of
    db/migrate.sh."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        assert conn.execute(text("SELECT to_regclass('needs.analysis_health')")).scalar_one() is not None
        assert conn.execute(
            text("SELECT has_table_privilege('needs_runtime', 'needs.analysis_health', 'SELECT')")
        ).scalar_one()
    engine.dispose()  # needs_migrator has CONNECTION LIMIT 2 -- released on pass or fail alike
