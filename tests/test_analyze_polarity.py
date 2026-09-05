"""One `analyze polarity` stage: source → need_mention · wish_mention, two runs give the same result and it
coexists with the seed."""

from __future__ import annotations

import csv
import re
import time
import urllib.error
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
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
from analysis.polarity.ownership import (
    _CRON_SCOPES,
    _GEMMA4_2026_08_24,
    ALWAYS,
    CRON_SINCE,
    NO_OWNERS,
    OWNERS,
    Owner,
    unready,
)
from analysis.polarity.pipeline import run
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

TUBEDEPTH_DDL = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "current" / "app.tubedepth.sql"
# Pouring the whole dump into one schema makes trend_radar and alembic_version collide — only the two tables
# it reads are stood up.
TUBEDEPTH_TABLES = ("comments", "video_snapshots")
# What opens SELECT on these five tables in production is db/grants/needs_runtime_reader.sql.
SOURCE_TABLES = ("review", "rank_snapshot", "product", *TUBEDEPTH_TABLES)
CAPTURED = datetime(2026, 8, 23, tzinfo=UTC)
WRITTEN = datetime(2026, 3, 4, tzinfo=UTC)
POSTED = datetime(2026, 3, 5, tzinfo=UTC)

# A source that produces the same (src, ref, need_key, sentence) as rows the seed already holds (measured on
# the dev DB: 400/400 slice-suncare reviews). Since 005 put extractor_version into the key this is no longer
# a natural-key collision — the two rows stay side by side. The seed values were read from need_mention and
# wish_mention as they are.
SEED_NEED = ("glowpick", "146765", "7856759", "146765/7856759", "끈적유분")
SEED_NEED_SENTENCE = "엄청 끈적이고 잘 안 발리고… 돈 더주고 좋은 거 살걸 그랬어요ㅠㅠ"
SEED_NEED_AT = datetime(2026, 8, 18, tzinfo=UTC)
SEED_WISH = ("--5yicxxgp4", "UgxrFMQux3xh1gzOnI94AaABAg")
SEED_WISH_TEXT = "스킨케어 루틴 찍어주세요"
SEED_WISH_AT = datetime(2026, 4, 22, tzinfo=UTC)
SEED_COUNTS = {"need_mention": 16046, "wish_mention": 18489}  # same source as tests/test_seed.py expects

# P1 은 선블록(suncare-v2.2 사전), P2 는 샴푸(p1-v2.2 사전) — 스코프 없는 실행의 기본 모양이다.
# One page of one month carries both lexicons, and need_rows calls them apart and puts them back.
REVIEWS = [
    ("oliveyoung", "R1", "P1", 5.0, "백탁이 하나도 없어서 진짜 좋아요", WRITTEN),
    ("oliveyoung", "R2", "P1", 1.0, "백탁이 너무 심해서 최악이에요", WRITTEN),
    ("oliveyoung", "R3", "P1", 5.0, "그냥 무난합니다", WRITTEN),
    # A review whose written_at is NULL — it falls back to captured_at and that count is recorded (formats.md
    # §Time).
    ("oliveyoung", "R4", "P1", 2.0, "끈적임이 심하고 밀려요", None),
    ("oliveyoung", "R5", "P2", 1.0, "비듬이 너무 심해서 최악이에요", WRITTEN),
]
SUNCARE_REVIEWS = 4  # the P1 ones among the five above
SHAMPOO_REVIEWS = 1
COMMENTS = [
    ("V1", "C1", "쿠션형으로도 출시해주세요 제발요", 12, POSTED),
    ("V1", "C2", "항상 잘 보고 있습니다 감사합니다", 3, POSTED),
    ("V1", "C3", "저는 백탁이 너무 심해서 못 쓰겠더라고요", 5, POSTED),
]


@pytest.fixture
def sources(needs_schema: str, trend_radar_schema: str, _schema_name: str) -> Iterator[str]:
    """needs + trend_radar + tubedepth are in one schema — production has three (arguments of run)."""
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
    # Source rows are inserted as the owner of that schema — needs_runtime has only SELECT on the source
    # (db/grants).
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
    """A run that names no ownership table runs with no owner — that is the behaviour from before ownership
    (#31)."""
    kwargs.setdefault("owners", NO_OWNERS)
    with connect(url) as conn:
        return run(conn, commerce_schema=schema, youtube_schema=schema, **kwargs)


def _rows(url: str, table: str) -> list[tuple[Any, ...]]:
    query = pgsql.SQL("SELECT * FROM {} ORDER BY src, ref, mention_id").format(pgsql.Identifier(table))
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(query)
        return [row[1:] for row in cur.fetchall()]  # mention_id is a bigserial and grows on every rerun


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
    assert found.captured_at_fallbacks == 1  # the one written_at NULL among REVIEWS
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
    """A fallback row sits in the month it was collected — since cuts on that value, so only the 2026-03
    review drops out."""
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
    """In production this stage runs as needs_runtime — SELECT on the source is granted by db/grants."""
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
    assert row is not None and row[0] == "needs_runtime"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connect(loaded) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE nope (i int)")


@pytest.fixture
def seeded(loaded: str, sources: str) -> Iterator[str]:
    """Every seed mention, plus the source rows that produce the same natural key as those seed rows."""
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
    """Re-extraction picks the same sentence and need_key as the seed again — since 005 put extractor_version
    into the natural key the two no longer collide and stay side by side, and the seed row's version tag is
    unchanged."""
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
    # How the seed survives has changed: it is the natural key that keeps the rows apart, not the WHERE of the
    # UPSERT. This review is one of the 548 slice-p1 re-extracted as well, so three versions stay side by side
    # (before, it was absorbed into the single suncare row).
    assert need == [
        ("rule-v2.3", "rule-v2.2"),
        ("slice-p1", "rule-v2.2"),
        ("slice-suncare", "rule-v2.1"),
    ]
    assert wish == [("slice-p9", "b")]
    assert {t: _tagged(seeded, t, "slice-%") for t in SEED_COUNTS} == SEED_COUNTS


class StubPolarity:
    """The classifier plugged into the place of a registered implementation — emitting a version other than
    the rules' is the point of this stub."""

    version = "stub-v9"

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="백탁", polarity="중립", reason="stub", version=self.version)

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]


def test_the_implementation_the_run_was_given_is_the_version_it_records(loaded: str, _schema_name: str):
    """versioning.md: analysis_run.versions records the versions of that run — they have to be those of the
    implementation that actually ran."""
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


# Rows left by an earlier run: places --scope will not write again (another category · another src).
OTHER_SCOPE = ("review", "P9/R9", "샴푸", "백탁")
OTHER_SRC = ("yt_comment", "V9/C9", None, "백탁")
# An old row left where this scope writes again. Its need_key differs, so the upsert cannot overwrite it in
# place — the natural key has no polarity_version, so a delete is the only thing that clears an old label.
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
    """Rows outside the scope are not written again by this run — deleting them makes them vanish from that
    month (every time, if it is a relabel)."""
    before = _refs(with_other_scopes, "need_mention")
    assert before == ["P9/R9", "V9/C9"]
    _run(with_other_scopes, _schema_name, scope="선블록", polarity=StubPolarity())
    assert _refs(with_other_scopes, "need_mention") == before


def test_a_scoped_run_does_not_delete_wish_rows_it_will_not_rewrite(
    with_other_scopes: str, _schema_name: str
):
    """--scope cuts by lexicon_category and wish_mention has no such column — a scoped run creates no wish row
    at all, so it must delete none."""
    _run(with_other_scopes, _schema_name, scope="선블록", polarity=StubPolarity())
    assert _refs(with_other_scopes, "wish_mention") == ["V9/C9"]


def _stale(url: str, ref: str) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT need_key, lexicon_category FROM need_mention WHERE ref = %s ORDER BY need_key", (ref,)
        )
        return cur.fetchall()


def test_a_scoped_run_deletes_its_own_scopes_stale_rows(with_other_scopes: str, _schema_name: str):
    """The natural key has no polarity_version, so the same need_key is an in-place upsert, but when a new
    classifier emits a different aspect the old need_key row stays as it is — and then aggregate counts one
    sentence twice. That is what the delete narrowed to the scope prevents."""
    _, ref, lexicon_category, need_key = SAME_SCOPE
    assert _stale(with_other_scopes, ref) == [(need_key, lexicon_category)]
    _run(with_other_scopes, _schema_name, scope="선블록", polarity=StubPolarity())
    assert _stale(with_other_scopes, ref) == []


def test_an_unscoped_rerun_still_replaces_this_units_own_stale_rows(
    with_other_scopes: str, _schema_name: str
):
    """Without a scope everything is written again — and then clearing the old version rows is still this
    stage's job."""
    _run(with_other_scopes, _schema_name, polarity=StubPolarity())
    assert _refs(with_other_scopes, "need_mention") == []


# The two limits of needs_runtime (db/bootstrap.sql: 60s · 15s) compressed so they are passed within seconds.
# The same idiom as tests/test_ollama_predictor_connection.py.
SQUEEZED_TIMEOUTS = "-c transaction_timeout=400ms -c idle_in_transaction_session_timeout=200ms"
EFFECTIVE_TIMEOUTS = (
    "SELECT current_setting('transaction_timeout'), current_setting('idle_in_transaction_session_timeout')"
)
SLOW_CALL_S = 0.5  # far longer than the two compressed limits — waiting inside a transaction dies here
OLLAMA_ANSWER = '{"aspect": "백탁", "polarity": "불만", "reason": "stub"}'


def _squeezed(base_url: str) -> str:
    url = make_url(base_url)
    existing = url.query.get("options", "")
    return url.update_query_dict({"options": f"{existing} {SQUEEZED_TIMEOUTS}".strip()}).render_as_string(
        hide_password=False
    )


def _probe_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The start probe is a night that passed (#32) — what the two below measure is the round trip that comes
    after it, and a GPU or a model can disappear even after the probe has gone through."""
    monkeypatch.setattr(OllamaPolarity, "preflight", lambda self: None)


def test_a_slow_classifier_never_waits_for_its_answer_inside_a_transaction(
    loaded: str, _schema_name: str, monkeypatch: pytest.MonkeyPatch
):
    """ollama waits from hundreds of ms to seconds per sentence (analysis/polarity/ollama.py). When that wait
    is inside an open transaction, both the stage's connection and the classifier's ledger connection are cut
    on the first page — the compressed limits reproduce that within seconds. No ollama and no GPU are needed:
    only the round trip is stubbed.
    """
    squeezed = _squeezed(loaded)
    # Confirm the compression actually took first — otherwise the assertions below pass for free.
    with connect(squeezed) as probe, probe.cursor() as cur:
        cur.execute(EFFECTIVE_TIMEOUTS)
        assert cur.fetchone() == ("400ms", "200ms")

    calls = 0

    def slow_post(self: OllamaPolarity, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        time.sleep(SLOW_CALL_S)
        return {"message": {"content": OLLAMA_ANSWER}, "prompt_eval_count": 7, "eval_count": 3}

    _probe_passes(monkeypatch)
    monkeypatch.setattr(OllamaPolarity, "_post", slow_post)
    monkeypatch.setattr(predictors, "LEXICON_URL", squeezed)  # send the ledger connection there too
    registry.load_implementations()

    with registry.open_classifier("polarity", "ollama:gemma4:latest") as polarity:
        found = _run(squeezed, _schema_name, polarity=polarity)
    # For this to be a reproduction the time spent classifying has to be well past the compressed limits (if
    # it is not, a pass means nothing).
    assert calls * SLOW_CALL_S > 1.0
    assert found.need_rows == calls and found.polarity_version.startswith("llm-ollama-gemma4")


UNREACHABLE = "ollama 가 응답하지 않는다"


def test_an_unreachable_ollama_closes_the_run_instead_of_leaving_it_running(
    loaded: str, _schema_name: str, monkeypatch: pytest.MonkeyPatch
):
    """A failed round trip (URLError · TimeoutError) is an OSError and so outside FAILURES in
    analysis/pipeline.py — unwrapped, the stage ends in a traceback and the run polarity opened stays open at
    'running' forever (analysis_health keeps reporting that run as still going). On the paid path _Blocking
    covers that place.
    """

    def refuse(self: OllamaPolarity, payload: dict[str, Any]) -> dict[str, Any]:
        raise urllib.error.URLError(UNREACHABLE)

    _probe_passes(monkeypatch)
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
    need_rows groups by lexicon, calls classify_many once per group and puts the results back by *global*
    index — putting them back by a group-local index attaches the later group's classifications to the earlier
    group's sentences and loses the later group's rows entirely.
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
# OWNERS is suspended empty (#242): GEMMA4 here names the implementation's own version, and the tests below
# exercise the ownership mechanism with an explicit local owners= table rather than the shipped one.
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")  # the shape of need_mention.month (formats.md)
GEMMA4 = OllamaPolarity().version
# The owner's row left where the rule run does not extract again — see whether the delete statement takes it.
OWNED_ONLY = ("P1/R7", "끈적유분", "gemma4 만 본 문장")
# 규칙 실행이 같은 자연키로 다시 쓰는 자리 — 005 의 자연키에 polarity_version 이 없어 제자리 upsert 가
# 주인의 라벨을 덮을 수 있다. 규칙은 이 문장을 '불만'으로 읽는다 (test_two_dictionaries... 참고).
CONTESTED = ("P1/R2", "백탁", "백탁이 너무 심해서 최악이에요")


def _label(
    url: str,
    ref: str,
    need_key: str,
    sentence: str,
    version: str,
    polarity: str = "만족",
    lexicon_category: str = "선블록",
    observed_at: str = "2026-03-04",
    month: str = "2026-03",
) -> None:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO need_mention (src, site, ref, lexicon_category, need_key, polarity,"
            " observed_at, observed_at_resolution, month, sentence, extractor_version,"
            " polarity_version) VALUES ('review', 'oliveyoung', %s, %s, %s, %s, %s,"
            " 'day', %s, %s, 'rule-v2.3', %s)",
            (ref, lexicon_category, need_key, polarity, observed_at, month, sentence, version),
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
    """Exactly the defect of today: a scope-less rule run wiped the gemma4 labels wholesale at 05:00 daily.
    OWNERS is suspended empty (#242), so this exercises the mechanism with an explicit owners= table rather
    than the shipped one — a registered owner's row still has to survive an unscoped rule run."""
    ref, need_key, sentence = OWNED_ONLY
    _label(loaded, ref, need_key, sentence, GEMMA4)
    owners = {SUNBLOCK: Owner(GEMMA4, ALWAYS)}
    with connect(loaded) as conn:
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name, owners=owners)
    assert _labels(loaded, ref) == [(need_key, "만족", GEMMA4)]


def test_an_unscoped_rule_run_does_not_overwrite_the_owners_label(loaded: str, _schema_name: str):
    """Where the rules extract the same sentence again — the natural key has no polarity_version, so even
    when the delete is escaped an in-place upsert swaps the owner's label for the rule label. A run that is
    not the owner does not classify that sentence at all. OWNERS is suspended empty (#242), so an explicit
    owners= table stands in for the shipped one."""
    ref, need_key, sentence = CONTESTED
    _label(loaded, ref, need_key, sentence, GEMMA4)
    owners = {SUNBLOCK: Owner(GEMMA4, ALWAYS)}
    with connect(loaded) as conn:
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name, owners=owners)
    assert _labels(loaded, ref) == [(need_key, "만족", GEMMA4)]


class OwnerPolarity:
    """선블록의 주인 자리에 꽂는 스텁 — 규칙과도 경쟁자와도 다른 버전을 내는 것이 요점이다.

    Records the sentences it classified: what an incremental run does *not* classify again cannot be seen from
    the rows alone — gemma4 is non-deterministic so a second classification is not guaranteed to give the same
    label, and the price is GPU time (#98).
    """

    version = "stub-owner-v9"

    def __init__(self) -> None:
        self.judged: list[str] = []

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        return PolarityResult(aspect="백탁", polarity="만족", reason="owner", version=self.version)

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        self.judged.extend(x.sentence for x in items)
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]


class RivalPolarity(OwnerPolarity):
    """The place of cron — it runs everything with no scope. Taking the owner's sentences too makes that label
    disappear."""

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
    """Regression guard: with the ownership table empty the behaviour is exactly today's — the later run takes
    everything."""
    _run(loaded, _schema_name, scope="선블록", polarity=OwnerPolarity(), owners=NO_OWNERS)
    _run(loaded, _schema_name, polarity=RivalPolarity(), owners=NO_OWNERS)
    assert _by_scope(loaded) == [
        ("샴푸", "불만", RivalPolarity.version),
        ("선블록", "불만", RivalPolarity.version),
    ]


def test_a_run_that_names_a_scope_it_does_not_own_is_refused(loaded: str, _schema_name: str):
    """`--scope 선블록` 을 --impl 없이 부르면 규칙이 주인의 자리를 도는 셈이다. 조용한 무동작이 아니라
    거절이어야 운영자가 표를 본다."""
    # OWNERS is suspended empty (#242): an explicit owners= table stands in for the shipped one so this
    # exercises the refusal mechanism regardless.
    owners = {SUNBLOCK: Owner(GEMMA4, ALWAYS)}
    with pytest.raises(ValueError, match=GEMMA4):
        _run(loaded, _schema_name, scope=SUNBLOCK, owners=owners)


def test_the_refusal_closes_the_stage_as_failed_instead_of_writing_nothing_quietly(
    loaded: str, _schema_name: str
):
    """The shape entrypoints.md §Analysis promises: a refusal stays as `analysis_run.status='failed'` and the
    CLI emits 1 — neither a run left open nor an exit code 0 as if nothing had happened.

    OWNERS is suspended empty (#242): an explicit owners= table stands in for the shipped one.
    """
    owners = {SUNBLOCK: Owner(GEMMA4, ALWAYS)}
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            scope=SUNBLOCK,
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            owners=owners,
        )
    assert found.status == "failed" and GEMMA4 in found.detail
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, finished_at IS NOT NULL FROM analysis_run")
        assert cur.fetchall() == [("failed", True)]


def test_owners_is_suspended_to_the_empty_table():
    """2026-09-06 (#242): the model host is gone, so OWNERS releases every gemma4 scope — same shape as
    NO_OWNERS, so scopes_of finds no foreign scope and the 05:00 rules line refreshes every scope again."""
    assert dict(OWNERS) == {}


def test_the_re_registration_recipe_still_names_the_version_the_implementation_actually_stamps():
    """The ownership-table check while OWNERS was populated: with the table empty this is the check that
    matters instead — a typo in the constants OWNERS is rebuilt from (#242) would leave a scope quietly
    ownerless the day the pass is re-registered."""
    assert _GEMMA4_2026_08_24 == OllamaPolarity().version


def test_the_re_registration_recipe_still_has_the_right_shape():
    """A typo that makes one line a different string leaves that category quietly ownerless (#31) — however
    many lines the recipe has, the revision it points at has to be one. CRON_SINCE has to be the same grain as
    need_mention.month: the predicate compares it with that column as a string, so a different shape goes
    quietly wrong (#97)."""
    assert MONTH.match(CRON_SINCE)
    assert len(_CRON_SCOPES) == len(set(_CRON_SCOPES)) == 27


# 저장된 lexicon_category 와 오늘의 매핑이 갈리는 자리 — rank_snapshot 의 최신 행과 category_map 이 매일
# 다시 계산하니 한 제품의 카테고리는 움직인다. 그때 주인의 행은 옛 scope 에 남고, 규칙은 같은 문장을 새
# scope 로 다시 뽑는다. P2/R5 는 오늘 '샴푸'로, 규칙은 그 문장에서 '트러블'을 낸다.
MOVED = ("P2/R5", "비듬이 너무 심해서 최악이에요")
RULE_KEY = "트러블"


def test_an_unscoped_rule_run_does_not_overwrite_an_owned_row_whose_scope_moved(
    loaded: str, _schema_name: str
):
    """When two implementations pick the same need_key the natural key (005) overlaps entirely — an in-place
    upsert swaps out the owner's row, the one that escaped the delete. The ownership predicate of the delete
    statement has to be in the update statement too. OWNERS is suspended empty (#242): an explicit owners=
    table stands in for the shipped one."""
    ref, sentence = MOVED
    _label(loaded, ref, RULE_KEY, sentence, GEMMA4)
    owners = {SUNBLOCK: Owner(GEMMA4, ALWAYS)}
    with connect(loaded) as conn:
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name, owners=owners)
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
    """The classifier of the second run — both its revision and its aspect differ from the earlier run. With
    the same aspect the old row would be refreshed by an in-place upsert and there would be no seeing whether
    the delete statement really catches it (need_key is in the natural key)."""

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
    """A comment row has no lexicon_category. With the shipped table standing, the ownership predicate of the
    delete statement asks `NULL <> ALL(...)`, and that value is NULL, so without the IS NULL branch an old
    revision row disappears under no rerun at all."""
    _run(loaded, _schema_name, polarity=StubPolarity(), owners=OWNERS)
    assert _comment_versions(loaded) == [StubPolarity.version]
    _run(loaded, _schema_name, polarity=DriftedPolarity(), owners=OWNERS)
    assert _comment_versions(loaded) == [DriftedPolarity.version]


# --- (scope, period) ownership (#97): registration is immediate, the past stays with the rules ----
# The reviews of loaded sit in two months: 2026-03 (written_at) and 2026-08 (written_at NULL → captured_at).
FUTURE = "2026-09"  # after those two months — registered, with the owner's pass not yet run once
OWNER_SINCE = "2026-08"  # the owner answers from August on: March stays with the rules
STALE_SHAMPOO = ("P2/R9", "백탁", "등록 전 규칙이 남긴 문장")


def _by_month(url: str) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT lexicon_category, month, polarity_version FROM need_mention WHERE src = 'review'"
            " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
        )
        return cur.fetchall()


def test_a_scope_registered_from_a_later_month_is_still_the_rules_until_then(loaded: str, _schema_name: str):
    """Ownership in #31 was the whole scope — registering 26 and deferring the pass created no row at all for
    new reviews in that category (where #84 got stuck). Cutting by period separates registration from the
    pass: before since, the rules keep writing and deleting."""
    owners = {"선블록": Owner(GEMMA4, ALWAYS), "샴푸": Owner(GEMMA4, FUTURE)}
    ref, need_key, sentence = STALE_SHAMPOO
    _label(loaded, ref, need_key, sentence, "rule-v0.9", "불만", "샴푸")
    with connect(loaded) as conn:
        run(conn, commerce_schema=_schema_name, youtube_schema=_schema_name, owners=owners)
    assert _by_month(loaded) == [("샴푸", "2026-03", RulePolarity.version)]
    assert _stale(loaded, ref) == []


def test_the_owner_leaves_the_months_before_its_since_to_the_rule(loaded: str, _schema_name: str):
    """It goes both ways: the rules leave the owner's period empty, and the owner neither writes nor deletes
    outside its own period.

    주인은 이제 `--scope` 없이 돌 수 있다 — 그 제약의 이유였던 '전량 재라벨'을 since 가 잘랐다.
    """
    owners = {"선블록": Owner(OwnerPolarity.version, OWNER_SINCE)}
    _run(loaded, _schema_name, owners=owners)  # the place of the 05:00 cron
    _run(loaded, _schema_name, polarity=OwnerPolarity(), owners=owners)  # the owner's pass
    assert _by_month(loaded) == [
        ("샴푸", "2026-03", RulePolarity.version),
        ("선블록", "2026-03", RulePolarity.version),
        ("선블록", "2026-08", OwnerPolarity.version),
    ]


def test_an_unscoped_owner_run_writes_nothing_that_has_no_lexicon_category(loaded: str, _schema_name: str):
    """Comments and wishes have no lexicon_category, so no owner holds — a scope-less run by the owner taking
    those rows makes the rule labels disappear and wipes the wishes of that month entirely."""
    owners = {"선블록": Owner(OwnerPolarity.version, OWNER_SINCE)}
    _run(loaded, _schema_name, owners=owners)
    wishes = _rows(loaded, "wish_mention")
    assert wishes, "위시 행이 없으면 이 단언은 진공이다"
    _run(loaded, _schema_name, polarity=OwnerPolarity(), owners=owners)
    assert _comment_versions(loaded) == [RulePolarity.version]
    assert _rows(loaded, "wish_mention") == wishes


def test_a_run_without_a_scope_must_own_one():
    """What decides whether `--impl` is let loose is not paid-or-not but 'does it have a place in the table':
    a scope-less line from an implementation outside the table is still a full relabel (cosmai/cli.py)."""
    assert unready(OWNERS, RulePolarity.version, None) is None
    # The owner runs its whole scope in one line — the period sets what that line covers, so it is not a full
    # relabel (#97). OWNERS is suspended empty (#242): an explicit owners= table stands in for the shipped
    # one for this one assertion.
    owners = {SUNBLOCK: Owner(GEMMA4, ALWAYS)}
    assert unready(owners, GEMMA4, None) is None
    assert "--scope" in str(unready(OWNERS, "stub-v9", None))
    # A category with no owner yet (a name not in OWNERS) — unblocked, it succeeds and is wiped at the next
    # 05:00.
    assert "ownership.py" in str(unready(OWNERS, GEMMA4, "미등록카테고리"))
    assert unready(owners, GEMMA4, SUNBLOCK) is None
    # Someone else's scope is not this function's job: the step refuses it with a failed run (entrypoints.md
    # §Analysis).
    assert unready(owners, "stub-v9", SUNBLOCK) is None
    # With OWNERS suspended for real (#242), the same line is refused instead -- there is no scope for it to
    # own until re-registration.
    assert "ownership.py" in str(unready(OWNERS, GEMMA4, SUNBLOCK))


# --- incremental run (#98): the owner classifies only "source rows with no row of my version" -----
# 늦게 도착한 리뷰. written_at 은 옛 달이라 롤링 창(`--since 어제`)이 못 잡고, 고정 컷은 컷 이후 전량을
# 매일 다시 판정한다 — 축이 written_at 이라 날짜로는 "안 한 것"을 고를 수 없다 (contracts/formats.md §시간).
LATE_REVIEW = ("oliveyoung", "R6", "P1", 1.0, "백탁이 진짜 심해서 못 쓰겠어요", WRITTEN)
# A row sitting in the month `--since D` falls in but earlier than D — without narrowing the delete with it,
# every run digs this one out.
BEFORE_SINCE = ("P7/R7", "끈적유분", "since 앞에 앉은 옛 행")
AFTER_SINCE = ("P7/R8", "끈적유분", "since 뒤에 앉은 옛 행")
SINCE_MONTH = "2026-08"  # the month the written_at NULL review of loaded sits in by captured_at
SINCE_DAY = date(2026, 8, 10)


def _mentions(url: str) -> list[tuple[Any, ...]]:
    """mention_id is read along with it — deleting and reinserting grows the value, so 'no row changed' stays
    visible."""
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT mention_id, ref, need_key, polarity, polarity_version FROM need_mention"
            " ORDER BY mention_id"
        )
        return cur.fetchall()


def _owner_only() -> dict[str, Owner]:
    return {"선블록": Owner(OwnerPolarity.version, ALWAYS)}


def test_a_repeated_missing_run_judges_nothing_and_leaves_every_row_untouched(loaded: str, _schema_name: str):
    """The command cron runs every day: with the source unchanged the second run never calls the classifier
    once. The row comparison looks at mention_id too — deleting and reinserting the same values is a
    reclassification as well."""
    owners = _owner_only()
    _run(loaded, _schema_name, polarity=OwnerPolarity(), owners=owners, missing=True)
    before = _mentions(loaded)
    assert before, "첫 실행이 아무 행도 안 썼으면 이 단언은 진공이다"
    again = OwnerPolarity()
    _run(loaded, _schema_name, polarity=again, owners=owners, missing=True)
    assert again.judged == []
    assert _mentions(loaded) == before


def test_a_missing_run_judges_only_the_source_row_that_has_no_row_of_its_version(
    loaded: str, needs_schema: str, _schema_name: str
):
    """Collection arrives late — the written_at of a new review is an old month. Only when what is picked is
    'a source row with no row of my version' rather than a date does it classify that one alone."""
    owners = _owner_only()
    _run(loaded, _schema_name, polarity=OwnerPolarity(), owners=owners, missing=True)
    before = _mentions(loaded)
    with connect(needs_schema) as conn, conn.cursor() as cur:
        source, key, product, rating, body, written = LATE_REVIEW
        cur.execute(
            "INSERT INTO review (source, review_key, captured_at, product_key, rating, body, written_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (source, key, CAPTURED, product, rating, body, written),
        )
        conn.commit()
    again = OwnerPolarity()
    _run(loaded, _schema_name, polarity=again, owners=owners, missing=True)
    after = _mentions(loaded)
    assert len(again.judged) == 1, again.judged
    assert [row[1] for row in after if row not in before] == ["P1/R6"]
    assert [row for row in before if row not in after] == []


def test_a_missing_run_adds_what_is_absent_and_replaces_nothing(loaded: str, _schema_name: str):
    """The incremental run only adds what is not there — swapping things out (a history correction, a raised
    revision) is the job of the --scope full path. So rows left by an old revision are not deleted in this
    mode."""
    ref, need_key, sentence = OWNED_ONLY
    _label(loaded, ref, need_key, sentence, "rule-v0.9")
    _run(loaded, _schema_name, polarity=OwnerPolarity(), owners=_owner_only(), missing=True)
    assert _labels(loaded, ref) == [(need_key, "만족", "rule-v0.9")]


def test_missing_is_refused_for_a_run_that_owns_no_scope(loaded: str, _schema_name: str):
    """Everything every day is right for the rules — with no ownership 'the rows of my version' is that whole
    population and the incremental run loses its meaning."""
    with pytest.raises(ValueError, match="--missing"):
        _run(loaded, _schema_name, missing=True)


def test_an_owner_run_does_not_walk_the_months_before_its_earliest_since(loaded: str, _schema_name: str):
    """An owner can write no row in the months before its since (the ownership predicate). Walking those
    months is the pure cost of one delete and one read — multiplied daily once 26 categories are taken out."""
    owners = {"선블록": Owner(OwnerPolarity.version, OWNER_SINCE)}
    found = _run(loaded, _schema_name, polarity=OwnerPolarity(), owners=owners)
    assert found.months == 1  # only 2026-08 of the two months of loaded


def test_since_does_not_delete_the_rows_that_sit_before_it(loaded: str, _schema_name: str):
    """`--since D` cut only the reads and not the delete — it deleted every row of the month D falls in from
    before D and rewrote only those after it. Put into cron as it was, it digs a hole every day."""
    ref, need_key, sentence = BEFORE_SINCE
    _label(loaded, ref, need_key, sentence, "rule-v0.9", observed_at="2026-08-01", month=SINCE_MONTH)
    _run(loaded, _schema_name, since=SINCE_DAY)
    assert _stale(loaded, ref) == [(need_key, "선블록")]


def test_since_still_deletes_the_stale_rows_it_will_rewrite(loaded: str, _schema_name: str):
    """The same run keeps the other direction too: after D is where this run writes again, so no old revision
    row may stay."""
    ref, need_key, sentence = AFTER_SINCE
    _label(loaded, ref, need_key, sentence, "rule-v0.9", observed_at="2026-08-20", month=SINCE_MONTH)
    _run(loaded, _schema_name, since=SINCE_DAY)
    assert _stale(loaded, ref) == []


# --- the 26 registrations + the cron line (#32) — the two move only in the same PR ----------------
REPO_ROOT = Path(__file__).resolve().parents[1]
CRONTAB_ANALYZE = REPO_ROOT / "stack" / "crontab.d" / "analyze"
CATEGORY_MAP_CSV = REPO_ROOT / "eval" / "lexicon" / "category_map_v1.csv"
# An evaluation set drawn from production rows, so its `category` column is the lexicon_category of those
# rows — the only place inside this repo where a name that is not in the mapping table (and so passes through
# as the identity) can be checked.
CROSSCAT_CSVS = ("crosscat_60.csv", "crosscat_blind40.csv")
SUNBLOCK = "선블록"


def _impl_lines(needle: str = "--impl") -> list[str]:
    """Non-comment crontab lines carrying `needle` — used to prove the `--impl` line stays gone while OWNERS
    is suspended (#242)."""
    return [
        line
        for raw in CRONTAB_ANALYZE.read_text(encoding="utf-8").splitlines()
        if (line := raw.split("#", 1)[0].strip()) and needle in line
    ]


def test_no_impl_line_runs_while_the_pass_is_suspended():
    """Suspended 2026-09-06 (#242): the crontab carries no `--impl` line while `OWNERS` is empty — a line with
    no registration would be an empty run."""
    assert _impl_lines() == []


def test_owners_names_a_gemma4_scope_iff_the_crontab_carries_an_impl_ollama_line():
    """The invariant #242 leaves behind: a registration with no line is a hole, a line with no registration is
    an empty run — both move together. Today both are absent; the day either comes back without the other
    this fails."""
    assert bool(OWNERS) == bool(_impl_lines("--impl ollama:"))


def test_the_re_registration_recipe_names_categories_this_repo_can_recognize():
    """The scopes the recipe (`_CRON_SCOPES` + the sunblock block) would restore have to be names
    `category_map` actually produces, and the same string as the evaluation set's category column — otherwise
    a re-registration would silently miss a category (#31, #97)."""
    with CATEGORY_MAP_CSV.open(encoding="utf-8") as handle:
        named = {row["lexicon_category"] for row in csv.DictReader(handle)}
    for name in CROSSCAT_CSVS:
        with (REPO_ROOT / "eval" / "polarity" / name).open(encoding="utf-8") as handle:
            named |= {row["category"] for row in csv.DictReader(handle)}
    recipe = set(_CRON_SCOPES) | {SUNBLOCK}
    assert len(named) > len(recipe) // 2, "collected no names -- this assertion would be vacuous"
    assert not named - recipe, f"named but missing from the recipe: {sorted(named - recipe)}"


class UnreachablePolarity(OwnerPolarity):
    """The classifier of a night with broken wiring — it dies at the probe and never sees one sentence."""

    def preflight(self) -> None:
        raise LookupError("ollama at http://nowhere:11434 did not answer; OLLAMA_URL names that address")


def test_a_probe_that_cannot_reach_the_model_fails_the_run_instead_of_labelling_nothing(
    loaded: str, _schema_name: str
):
    """A quiet failure is the illness this issue stepped on three times: a night it could not reach is a
    failed run rather than "0 rows, success", and `cosmai/cli.py` moves that state to exit code 1
    (entrypoints.md §Analysis)."""
    judge = UnreachablePolarity()
    owners = {SUNBLOCK: Owner(judge.version, ALWAYS)}
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=judge,
            owners=owners,
            missing=True,
        )
    assert found.status == "failed" and "OLLAMA_URL" in found.detail
    assert judge.judged == [], "프로브가 죽었는데 문장이 판정됐다면 프로브가 시작 자리에 없는 것이다"
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, finished_at IS NOT NULL FROM analysis_run")
        assert cur.fetchall() == [("failed", True)]
