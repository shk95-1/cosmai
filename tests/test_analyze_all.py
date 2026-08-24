"""`cosmai analyze all`: 세 단계가 한 run 을 공유하고, 두 번째 실행이 같은 metrics 를 낸다 (#5).

원천 두 스키마는 계약 덤프 그대로 따로 세운다 — 운영에서 needs·trend_radar·tubedepth 가 세 스키마다.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
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
from cosmai.cli import main
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPS = REPO_ROOT / "contracts" / "ddl" / "current"
VIEW = REPO_ROOT / "db" / "views" / "analysis_health.sql"
CRONTAB = REPO_ROOT / "stack" / "crontab"
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
        polarity = pipeline.run_stage(conn, "polarity", commerce_schema=commerce, youtube_schema=youtube)
        aggregate = pipeline.run_stage(conn, "aggregate", commerce_schema=commerce, captured_at=CAPTURED_DATE)
    assert link.status == "ok" and link.counts["product_ref"] > 0
    assert polarity.status == "ok" and polarity.counts["attempted_need"] > 0
    assert aggregate.status == "ok" and aggregate.counts["metrics_need"] > 0


def test_the_cli_exits_one_when_a_stage_fails_and_two_when_it_cannot_connect(
    analysis_url: str, capsys: pytest.CaptureFixture[str]
):
    # 이 컨테이너에는 trend_radar 스키마가 없다 — link 가 거기서 실패한다.
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
    assert _time(CRONTAB.read_text(encoding="utf-8")) == contract


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
