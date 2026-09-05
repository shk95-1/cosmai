"""두 analyze 실행이 겹치는 자리 — 남의 run 을 닫지 않고, 겹치면 양보하고, 반쯤 쓴 달을 말한다 (#16).

운영에서 겹치는 두 줄이 정상 시나리오다: 05:00 크론의 `cosmai analyze all`(규칙)과 사람이 손으로 도는
2.5~4시간짜리 `analyze polarity --impl ollama:gemma4:latest --scope 선블록`.

analyze 락은 데이터베이스 단위이고 스키마 단위가 아니다 — tests/collectors/commerce/test_source_lock.py
와 같은 이유로 이 파일의 테스트들은 서로에게 두 번째 크론 줄이 될 수 있다. pytest 는 한 번에 하나씩
돌리고, 스위트를 병렬화하는 무엇이든 이 둘을 떼어 놓아야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from analysis.locks import ANALYZE, advisory_key, analyze_lock
from analysis.pipeline import run_stage
from analysis.polarity import RulePolarity
from analysis.polarity.ownership import NO_OWNERS, OWNERS
from analysis.types import AspectLexicon, PolarityRequest, PolarityResult
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

CAPTURED = datetime(2026, 8, 23, tzinfo=UTC)
WRITTEN = datetime(2026, 3, 4, tzinfo=UTC)
MONTH = "2026-03"
SUNBLOCK = "선블록"
GEMMA4 = OWNERS[SUNBLOCK].version
BOOM = "polarity boom"
SOURCE_TABLES = ("review", "rank_snapshot", "product")
TUBEDEPTH_DDL = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "current" / "app.tubedepth.sql"
# Pouring the whole dump into one schema collides with trend_radar -- only the two tables polarity reads are
# stood up.
TUBEDEPTH_TABLES = ("comments", "video_snapshots")
# link walks all three of titles, transcripts and comments (DOCUMENTS in analysis/linker/pipeline.py).
LINK_TUBEDEPTH_TABLES = ("comments", "video_snapshots", "transcripts")
# The other two tables run_ranking of aggregate reads (analysis/aggregate/ranking.py).
RANKING_TABLES = ("price_point", "review_stats")

# P1 은 선블록(주인 있는 scope), P2 는 샴푸(주인 없는 scope) — 한 페이지에 둘 다 실려야 규칙 실행도
# 스코프 실행도 판정을 한 번은 부른다.
REVIEWS = (
    ("oliveyoung", "R1", "P1", 1.0, "백탁이 너무 심해서 최악이에요", WRITTEN),
    ("oliveyoung", "R2", "P1", 5.0, "백탁이 하나도 없어서 진짜 좋아요", WRITTEN),
    ("oliveyoung", "R3", "P2", 1.0, "비듬이 너무 심해서 최악이에요", WRITTEN),
)
PRODUCTS = (
    ("P1", "테스트 선크림 SPF50", "suncare", "스킨케어 > 선크림"),
    ("P2", "테스트 샴푸 500ml", "haircare", "헤어케어 > 샴푸"),
)

RIVAL_NOTE = f"analyze:polarity:{GEMMA4}"
RULE_NOTE = f"analyze:polarity:{RulePolarity.version}"
RIVAL_VERSIONS = '{"polarity": "%s"}' % GEMMA4  # noqa: UP031 - a jsonb literal reads badly as an f-string
OPEN_RUN = "INSERT INTO analysis_run (versions, note) VALUES (%s::jsonb, %s) RETURNING run_id"
GRANTED = (
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = %s AND objid = %s "
    "AND objsubid = 2 AND granted"
)


@pytest.fixture
def loaded(needs_schema: str, trend_radar_schema: str, _schema_name: str, needs_runtime_url: str) -> str:
    """needs + trend_radar are in one schema -- in production they are two (the arguments of run_stage).

    tubedepth is not stood up: this file cares about overlap, and without the comments table the YouTube
    branch of polarity is skipped wholesale (`_exists`).
    """
    seed.run_all(needs_runtime_url, only=("lexicon",))
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            for table in SOURCE_TABLES:
                conn.exec_driver_sql(f'GRANT SELECT ON "{_schema_name}"."{table}" TO needs_runtime')
    finally:
        engine.dispose()
    with connect(needs_schema) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO product (source, product_key, captured_at, name, first_seen_at, last_seen_at)"
            " VALUES ('oliveyoung', %s, %s, %s, %s, %s)",
            [(key, CAPTURED, name, CAPTURED, CAPTURED) for key, name, _, _ in PRODUCTS],
        )
        cur.executemany(
            "INSERT INTO rank_snapshot"
            " (source, board, category_key, product_key, captured_at, category_name, rank, product_name)"
            " VALUES ('oliveyoung', 'best', %s, %s, %s, %s, 1, %s)",
            [(board, key, CAPTURED, category, name) for key, name, board, category in PRODUCTS],
        )
        cur.executemany(
            "INSERT INTO review (source, review_key, captured_at, product_key, rating, body, written_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(s, k, CAPTURED, p, r, b, w) for s, k, p, r, b, w in REVIEWS],
        )
        conn.commit()
    return needs_runtime_url


@pytest.fixture
def half_wired_youtube(loaded: str, needs_schema: str, _schema_name: str) -> str:
    """It stands the two tubedepth tables up but does not open SELECT on `video_snapshots`.

    Missing the grant of a new table in production (db/grants/needs_runtime_reader.sql) makes the yt_comment
    branch of polarity die exactly at `_channels` with a psycopg.Error -- right after it has used up the
    review branch.
    """
    dump = TUBEDEPTH_DDL.read_text(encoding="utf-8")
    ddl = "\n".join(
        dump.split(f"CREATE TABLE tubedepth.{table} (")[1]
        .split(");")[0]
        .join((f'CREATE TABLE "{_schema_name}"."{table}" (', ");"))
        for table in TUBEDEPTH_TABLES
    )
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(ddl)
            conn.exec_driver_sql(f'GRANT SELECT ON "{_schema_name}"."comments" TO needs_runtime')
    finally:
        engine.dispose()
    return loaded


def _open(url: str, schema: str, table: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'GRANT SELECT ON "{schema}"."{table}" TO needs_runtime')
    finally:
        engine.dispose()


class _Interrupt:
    """It calls `during` on the first classification call and dies if told to -- it makes the moment two runs
    are open at once deterministic. A callback rather than a thread because the cause of the defect is the
    state of the overlap, not its timing."""

    def __init__(self, during: Callable[[], None], version: str, explode: bool = True) -> None:
        self.version = version
        self._during = during
        self._explode = explode
        self.fired = 0

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        del sentence, rating, category, aspects
        return PolarityResult(aspect="백탁", polarity="불만", reason="stub", version=self.version)

    def classify_many(self, items: Sequence[PolarityRequest], aspects: AspectLexicon) -> list[PolarityResult]:
        self.fired += 1
        if self.fired == 1:
            self._during()
            if self._explode:
                raise ValueError(BOOM)
        return [self.classify(x.sentence, x.rating, x.category, aspects) for x in items]


def _rows(url: str) -> dict[int, tuple[str, str, bool]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT run_id, status, coalesce(note, ''), finished_at IS NOT NULL FROM analysis_run")
        return {int(r[0]): (r[1], r[2], r[3]) for r in cur.fetchall()}


def _open_rival(url: str, note: str) -> int:
    """A run someone else's execution already opened -- nobody may touch this row."""
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(OPEN_RUN, (RIVAL_VERSIONS, note))
        row = cur.fetchone()
        conn.commit()
    assert row is not None
    return int(row[0])


@pytest.fixture
def held_elsewhere(runtime_url_for_tests: str) -> Iterator[None]:
    """Another process holds the analyze lock. It is session-scoped, so it lasts as long as the connection."""
    classid, objid = advisory_key(ANALYZE)
    with connect(runtime_url_for_tests) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (classid, objid))
            assert cur.fetchone() == (True,)
        try:
            yield
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s, %s)", (classid, objid))


# --- Defect 1: a run closes only the run it opened itself ----------------------------------------


def test_a_dying_manual_pass_leaves_the_rule_run_it_overlapped_alone(loaded: str, _schema_name: str):
    """수동 패스(gemma4, --scope 선블록)가 죽는다. 그 사이 05:00 규칙 run 이 열려 있다.

    ORPHAN_RUN 의 `ORDER BY run_id DESC LIMIT 1` 은 항상 더 나중에 열린 쪽을 고른다 — 즉 남의 run 이다.
    """
    rival: list[int] = []
    polarity = _Interrupt(lambda: rival.append(_open_rival(loaded, RULE_NOTE)), GEMMA4)
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            scope=SUNBLOCK,
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=polarity,
            owners=OWNERS,
        )
    assert found.status == "failed" and BOOM in found.detail
    rows = _rows(loaded)
    assert rival, "the stub never ran, so nothing overlapped"
    # Someone else's run is still going and its note is unchanged.
    assert rows[rival[0]] == ("running", RULE_NOTE, False)
    mine = [run_id for run_id in rows if run_id != rival[0]]
    assert len(mine) == 1, rows
    # And its own run is not left open.
    assert rows[mine[0]][0] == "failed" and rows[mine[0]][2]


def test_a_dying_rule_run_leaves_the_manual_passs_run_alone(loaded: str, _schema_name: str):
    """반대 방향: 05:00 규칙 run 이 죽고, 그 사이 사람이 연 수동 패스 run 이 열려 있다.

    수동 패스는 gemma4 가 라벨한 4시간짜리다 — 남이 그 run 을 failed 로 닫으면서 versions.polarity 를
    규칙 버전으로 덮으면 재현의 유일한 단서가 거짓이 된다 (contracts/versioning.md).
    """
    rival: list[int] = []
    polarity = _Interrupt(lambda: rival.append(_open_rival(loaded, RIVAL_NOTE)), RulePolarity.version)
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=polarity,
            owners=OWNERS,
        )
    assert found.status == "failed" and BOOM in found.detail
    rows = _rows(loaded)
    assert rival, "the stub never ran, so nothing overlapped"
    assert rows[rival[0]] == ("running", RIVAL_NOTE, False)
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions ->> 'polarity' FROM analysis_run WHERE run_id = %s", (rival[0],))
        assert cur.fetchone() == (GEMMA4,)


def test_a_failed_solo_pass_keeps_the_version_it_was_labelling_with(loaded: str, _schema_name: str):
    """단독 패스가 죽어도 그 run 의 `versions.polarity` 는 그 패스가 쓰던 판본이어야 한다.

    `analysis_health.polarity_version` 이 그 값을 그대로 읽는다 (db/views/analysis_health.sql) —
    닫는 쪽이 빈 versions 로 덮으면 4시간짜리 gemma4 패스가 무엇으로 라벨하다 죽었는지 표에 남지
    않는다 (contracts/versioning.md).
    """
    polarity = _Interrupt(lambda: None, GEMMA4)
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            scope=SUNBLOCK,
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=polarity,
            owners=OWNERS,
        )
    assert found.status == "failed" and BOOM in found.detail
    assert found.run_id is not None
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, versions ->> 'polarity' FROM analysis_run WHERE run_id = %s", (found.run_id,)
        )
        assert cur.fetchone() == ("failed", GEMMA4)


# --- Defect 3: one lock stops two analyze runs overlapping -----------------------------------------


def test_an_analyze_run_holds_the_analyze_lock_while_a_stage_works(loaded: str, _schema_name: str):
    """Is the lock really taken -- another session reads pg_locks while a stage is running."""
    classid, objid = advisory_key(ANALYZE)
    seen: list[int] = []

    def observe() -> None:
        with connect(loaded) as conn, conn.cursor() as cur:
            cur.execute(GRANTED, (classid, objid & 0xFFFFFFFF))
            row = cur.fetchone()
            seen.append(int(row[0]) if row else 0)

    polarity = _Interrupt(observe, RulePolarity.version, explode=False)
    back: list[int] = []
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=polarity,
            owners=NO_OWNERS,
        )
        # Whether it was released has to be asked while the connection holding it is alive -- the server
        # reclaims it when the session closes, so pg_locks after closing is 0 with or without the unlock (a
        # vacuous assertion). Reacquiring on the same session cannot catch it either:
        # pg_try_advisory_lock is reentrant and granted is 1 even taken twice.
        with conn.cursor() as cur:
            cur.execute(GRANTED, (classid, objid & 0xFFFFFFFF))
            row = cur.fetchone()
            back.append(int(row[0]) if row else -1)
    assert found.status == "ok", found.detail
    assert seen == [1], "the analyze lock was not held while the stage was writing"
    assert back == [0], "the analyze lock was still held after the stage returned"


def test_a_lock_that_went_missing_mid_run_is_said_out_loud(
    runtime_url_for_tests: str, capsys: pytest.CaptureFixture[str]
):
    """The same one line as the collectors (collectors/commerce/storage/locks.py): if `pg_advisory_unlock` is
    false the lock went during the run, and on a 2.5-4 hour run that one line is the only evidence after the
    fact of "the two may have overlapped".
    """
    classid, objid = advisory_key(ANALYZE)
    with connect(runtime_url_for_tests) as conn, analyze_lock(conn) as held:
        assert held
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (classid, objid))
            assert cur.fetchone() == (True,)
        conn.commit()
    assert "did not hold it" in capsys.readouterr().out


def test_a_second_analyze_run_yields_instead_of_interleaving(
    loaded: str, _schema_name: str, held_elsewhere: None
):
    """The same shape as a collector (contracts/entrypoints.md §Collectors): if it cannot take the lock it
    skips, leaves a reason and ends partial (1). It does not wait — the 05:00 queued behind a four-hour pass
    is still in the queue at the next 05:00."""
    with connect(loaded) as conn:
        found = run_stage(conn, "aggregate", commerce_schema=_schema_name)
    assert found.status == "partial", found.status
    assert "skipped" in found.note and ANALYZE in found.note
    with connect(loaded) as conn, conn.cursor() as cur:
        # The reason stays not only in the cron mail but in the production view (analysis_health).
        cur.execute("SELECT status, note FROM analysis_run")
        rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM metrics_need")
        assert cur.fetchone() == (0,)
    assert len(rows) == 1 and rows[0][0] == "partial" and "skipped" in rows[0][1]


def test_a_polarity_pass_that_cannot_take_the_lock_writes_no_rows(
    loaded: str, _schema_name: str, held_elsewhere: None
):
    """A run that yielded deletes not one month -- withdrawing half-deleted would create defect 2 itself."""
    with connect(loaded) as conn:
        found = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=NO_OWNERS
        )
    assert found.status == "partial", found.status
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM need_mention")
        assert cur.fetchone() == (0,)


# --- Defect 2: a half-rewritten month is not silent -------------------------------------------------


def test_the_month_being_rewritten_is_named_on_the_open_run(loaded: str, _schema_name: str):
    """`replace_stale` deletes a month, commits, and rewrites it page by page -- in between is the window
    where only part of it is left. That it is inside that window has to be said by the DB itself."""
    seen: list[str] = []

    def observe() -> None:
        with connect(loaded) as conn, conn.cursor() as cur:
            cur.execute("SELECT note FROM analysis_run WHERE status = 'running' ORDER BY run_id")
            seen.extend(r[0] or "" for r in cur.fetchall())

    polarity = _Interrupt(observe, RulePolarity.version, explode=False)
    with connect(loaded) as conn:
        found = run_stage(
            conn,
            "polarity",
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=polarity,
            owners=NO_OWNERS,
        )
    assert found.status == "ok", found.detail
    assert seen == [f"{RULE_NOTE} rewriting=review/{MONTH}"], seen
    # Once it is fully written the mark disappears -- a finished month must not keep looking partial.
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analysis_run WHERE note LIKE '%rewriting=%'")
        assert cur.fetchone() == (0,)


def test_a_month_left_half_written_by_a_dead_pass_is_reported_not_swallowed(loaded: str, _schema_name: str):
    """수동 패스가 그 창 안에서 죽으면 그 달의 선블록 행은 부분만 남는다. 규칙은 그 scope 를 배제하므로
    (#31) 사람이 다시 돌릴 때까지 아무도 메우지 않는다 — 최소한 조용하지는 않아야 한다.

    analyze 락을 쥔 동안 열려 있는 `rewriting=` 표식은 죽은 실행의 것뿐이다: 그것이 이 판정의 근거다.
    """
    dead = _open_rival(loaded, f"{RIVAL_NOTE} rewriting=review/{MONTH}/{SUNBLOCK}")
    with connect(loaded) as conn:
        found = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=OWNERS
        )
    assert found.status == "partial", found.status
    assert f"review/{MONTH}/{SUNBLOCK}" in found.note
    rows = _rows(loaded)
    # That run is closed rather than running forever. The mark stays in the note and keeps saying which
    # month.
    assert rows[dead][0] == "failed" and rows[dead][2]
    assert f"rewriting=review/{MONTH}/{SUNBLOCK}" in rows[dead][1]


def test_a_caught_failure_leaves_a_half_month_the_next_run_finds(loaded: str, _schema_name: str):
    """실무에서 가장 흔한 죽음은 잡히는 죽음이다 — ollama 예외·statement_timeout 은 FAILURES 로 잡혀
    run 이 `failed` 로 닫히고, `running` 만 보는 조회는 그 죽음이 남긴 반쪽 달을 통째로 놓친다.

    그리고 그 달은 아무도 메우지 않는다: 선블록은 주인이 있어 규칙 실행이 배제한다 (#31).
    """
    polarity = _Interrupt(lambda: None, GEMMA4)
    with connect(loaded) as conn:
        died = run_stage(
            conn,
            "polarity",
            scope=SUNBLOCK,
            commerce_schema=_schema_name,
            youtube_schema=_schema_name,
            polarity=polarity,
            owners=OWNERS,
        )
    assert died.status == "failed" and died.run_id is not None
    assert f"rewriting=review/{MONTH}/{SUNBLOCK}" in _rows(loaded)[died.run_id][1]
    # The next night's rule run finds that mark and names it.
    with connect(loaded) as conn:
        next_night = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=OWNERS
        )
    assert next_night.status == "partial", next_night.note
    assert f"review/{MONTH}/{SUNBLOCK}" in next_night.note
    # It is said once -- the month is not filled until the owner runs again, and emitting the same partial
    # every night would make partial mean nothing.
    with connect(loaded) as conn:
        after = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=OWNERS
        )
    assert after.status == "ok", after.note


def test_a_month_finished_before_the_run_died_is_not_called_stale(
    half_wired_youtube: str, needs_schema: str, _schema_name: str
):
    """리뷰 가지가 마지막 달을 다 쓴 뒤 yt_comment 가지에서 죽는 경우 — 그 달은 완성됐다.

    표식이 거기 남으면 다음 실행이 멀쩡한 달을 반쪽이라 부르고, 사람은 다시 돌릴 것 없는 4시간짜리
    패스를 다시 돈다.
    """
    with connect(half_wired_youtube) as conn:
        died = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=NO_OWNERS
        )
    assert died.status == "failed" and "permission denied" in died.detail
    assert died.run_id is not None
    assert "rewriting=" not in _rows(half_wired_youtube)[died.run_id][1]
    # Fill the grant and the next run succeeds -- and does not call a fully written month stale.
    _open(needs_schema, _schema_name, "video_snapshots")
    with connect(half_wired_youtube) as conn:
        found = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=NO_OWNERS
        )
    assert found.status == "ok", found.note


# --- Defect 4: the partial of a standalone stage run stays in the production view -------------------
# The exit code is only in the cron mail. Where the contract says "what the operator looks at is that row" is
# analysis_run, and the run that finds the one kind of hole nobody fills (a half month of an owned scope) is
# exactly the standalone run.


@pytest.fixture
def wired_youtube(loaded: str, needs_schema: str, _schema_name: str) -> str:
    """All three tubedepth tables link reads are standing and open (DOCUMENTS in
    analysis/linker/pipeline.py)."""
    dump = TUBEDEPTH_DDL.read_text(encoding="utf-8")
    ddl = "\n".join(
        dump.split(f"CREATE TABLE tubedepth.{table} (")[1]
        .split(");")[0]
        .join((f'CREATE TABLE "{_schema_name}"."{table}" (', ");"))
        for table in LINK_TUBEDEPTH_TABLES
    )
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(ddl)
            for table in LINK_TUBEDEPTH_TABLES:
                conn.exec_driver_sql(f'GRANT SELECT ON "{_schema_name}"."{table}" TO needs_runtime')
    finally:
        engine.dispose()
    return loaded


def _reported(url: str, dead: int) -> list[tuple[str, str, bool]]:
    """The partial row that names that month, among the rows that are not the dead run."""
    return [
        row
        for run_id, row in _rows(url).items()
        if run_id != dead and row[0] == "partial" and f"review/{MONTH}/{SUNBLOCK}" in row[1]
    ]


def test_a_solo_polarity_pass_closes_its_own_run_as_partial(loaded: str, _schema_name: str):
    """주인의 gemma4 패스가 정확히 이 경로다: `analyze polarity --scope 선블록`.

    그 run 은 polarity 가 자기 손으로 `ok` 로 닫은 뒤다 — 구멍을 찾은 실행이 그 행을 다시 닫지 않으면
    analysis_health 에는 `ok` 만 남고, 종료 코드 1 은 크론 메일과 함께 사라진다.
    """
    dead = _open_rival(loaded, f"{RIVAL_NOTE} rewriting=review/{MONTH}/{SUNBLOCK}")
    with connect(loaded) as conn:
        found = run_stage(
            conn, "polarity", commerce_schema=_schema_name, youtube_schema=_schema_name, owners=OWNERS
        )
    assert found.status == "partial" and found.run_id is not None
    status, note, finished = _rows(loaded)[found.run_id]
    assert status == "partial", f"analysis_health still says {status} for run {found.run_id}"
    assert f"review/{MONTH}/{SUNBLOCK}" in note and finished
    assert dead != found.run_id
    # The revision is unchanged -- if the closing side overwrote versions, what it labelled with would
    # disappear from the view.
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions ->> 'polarity' FROM analysis_run WHERE run_id = %s", (found.run_id,))
        assert cur.fetchone() == (RulePolarity.version,)


def test_a_solo_aggregate_run_reports_the_half_month_without_taking_its_notes_key(
    loaded: str, needs_schema: str, _schema_name: str
):
    """The note of aggregate is the natural key `_run_id` uses to find the run again -- writing the report
    there leaves the next run unable to find that row and piling up another run. So the report stays as a row
    of its own, in the same place as the lock yield.
    """
    for table in RANKING_TABLES:
        _open(needs_schema, _schema_name, table)
    with connect(loaded) as conn:
        # aggregate fails when there is no mention for it to count -- a rule pass labels one month first.
        assert (
            run_stage(
                conn,
                "polarity",
                commerce_schema=_schema_name,
                youtube_schema=_schema_name,
                owners=NO_OWNERS,
            ).status
            == "ok"
        )
    dead = _open_rival(loaded, f"{RIVAL_NOTE} rewriting=review/{MONTH}/{SUNBLOCK}")
    with connect(loaded) as conn:
        found = run_stage(conn, "aggregate", commerce_schema=_schema_name)
    assert found.status == "partial", found.note
    assert _reported(loaded, dead), _rows(loaded)
    notes = [note for _, note, _ in _rows(loaded).values()]
    assert any(note.startswith("aggregate:") for note in notes), notes


def test_a_solo_link_run_reports_the_half_month_it_found(wired_youtube: str, _schema_name: str):
    """link has no run row of its own -- the hole still has to be said, and the place to say it is the place
    the run that yielded the lock already writes (one partial row of analysis_run)."""
    dead = _open_rival(wired_youtube, f"{RIVAL_NOTE} rewriting=review/{MONTH}/{SUNBLOCK}")
    with connect(wired_youtube) as conn:
        found = run_stage(conn, "link", commerce_schema=_schema_name, youtube_schema=_schema_name)
    assert found.status == "partial", found.note
    assert _reported(wired_youtube, dead), _rows(wired_youtube)
