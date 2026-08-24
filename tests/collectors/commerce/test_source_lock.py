"""Two cron lines, one site: whoever gets there second yields the source instead of doubling its rate.

`collectors/commerce` enforces its rate policy per `Gate`, and a `Gate` is built inside `collect()` --
per lane, per process. Nothing above it coordinated, so `0 * * * * ranking` overlapping a daily walk
sent oliveyoung (a browser transport) twice the requests its policy allows. #10 §A-8-1 chose a
Postgres session-scope advisory lock per source: not taken means skip that source, record why, and
end the run partial (exit 1) -- we yielded, the site did not refuse.

Every assertion here is on observed Postgres state (`pg_locks`) or on what `collect()` did with a
second connection genuinely holding the lock. The three traps this file exists to not fall into:
a lock the code never actually takes, a lock scoped to a transaction that ends before the walk does,
and a lock whose session the runtime role's `idle_in_transaction_session_timeout` kills mid-walk.

Advisory locks are per database, not per schema, so unlike the rest of the suite these tests are not
isolated by the per-test schema -- two of them running at once would be each other's second cron
line. pytest runs them one at a time; anything that parallelises the suite has to keep them apart.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from collectors.commerce import cli
from collectors.commerce.contract import Fetch, Payload, Scope, Source, SourcePolicy, Yield
from collectors.commerce.engine import collect, exit_code_for
from collectors.commerce.models import Dataset
from collectors.commerce.registry import SOURCES
from collectors.commerce.storage import locks
from collectors.commerce.storage.locks import LOCK_CLASS, PostgresSourceLock, advisory_key
from collectors.commerce.storage.tables import run as run_table
from collectors.commerce.storage.tables import run_source as run_source_table

REPO_ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 8, 24, 3, tzinfo=UTC)

# The two transaction timeouts the runtime role carries -- db/bootstrap.sql sets them on needs_runtime
# (the role these tests use) and production's trend_radar_runtime carries the same pair from outside
# this repo (collectors/commerce/storage/locks.py) -- compressed so a test can outlive them in under a
# second. A lock connection that sits in a transaction dies to the first of these; one in autocommit
# does not notice either.
SQUEEZED_TIMEOUTS = "-c idle_in_transaction_session_timeout=200ms -c transaction_timeout=400ms"
IDLE_KILL_MARGIN_S = 0.8  # 4x the timeout above
FREED_TIMEOUT_S = 5.0
EFFECTIVE_IDLE_TIMEOUT = "SELECT current_setting('idle_in_transaction_session_timeout')"

POLICY = SourcePolicy(min_interval_s=0.0, concurrency=1)


def _source(source_key: str) -> Source:
    """A source with nothing site-specific about it, named so the fetcher can say who asked. A class
    per key because `Source.key` is a ClassVar, the same reason test_rate_policy_is_enforced.py builds
    one class per policy."""

    class _FakeSource:
        key: ClassVar[str] = source_key
        datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING})
        scope: ClassVar[Scope] = {Dataset.RANKING: {"seeds": 1}}
        policy: ClassVar[SourcePolicy] = POLICY

        def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
            del board
            return (Fetch(url=f"https://example.invalid/{source_key}/0", dataset=dataset),)

        def parse(self, payload: Payload) -> Yield:
            del payload
            return Yield()

    return _FakeSource()


class _NullSink:
    def write(self, records) -> None:
        return None


class _RecordingFetcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.urls: list[str] = []

    def fetch(self, fetch: Fetch) -> Payload:
        with self._lock:
            self.urls.append(fetch.url)
        return Payload(fetch=fetch, status=200, body=b"{}", final_url=fetch.url, headers={}, elapsed_ms=1)


class _NeverFetcher:
    """Any request at all is the failure: this test's only source is locked by someone else."""

    def fetch(self, fetch: Fetch) -> Payload:
        raise AssertionError(f"a locked source was walked anyway: {fetch.url}")


GRANTED = text(
    "SELECT l.pid, a.state FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
    "WHERE l.locktype = 'advisory' AND l.classid = :classid AND l.objid = :objid "
    "AND l.objsubid = 2 AND l.granted"
)


def _granted(observer: sa.Connection, source_key: str) -> list[sa.Row]:
    """The backends holding this source's lock, straight out of the lock manager.

    `pg_locks.objid` is an oid -- unsigned -- while `pg_try_advisory_lock` takes int4, so the key the
    lock was taken with has to be read back as the same 32 bits without the sign.
    """
    classid, objid = advisory_key(source_key)
    return list(observer.execute(GRANTED, {"classid": classid, "objid": objid & 0xFFFFFFFF}).fetchall())


def _take(connection: sa.Connection, source_key: str) -> bool:
    classid, objid = advisory_key(source_key)
    return bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(:classid, :objid)"), {"classid": classid, "objid": objid}
        ).scalar_one()
    )


def _release(connection: sa.Connection, source_key: str) -> None:
    classid, objid = advisory_key(source_key)
    connection.execute(
        text("SELECT pg_advisory_unlock(:classid, :objid)"), {"classid": classid, "objid": objid}
    )


def _free(connection: sa.Connection, source_key: str) -> bool:
    """Could someone else take it right now? Gives it straight back so the answer costs nothing."""
    if not _take(connection, source_key):
        return False
    _release(connection, source_key)
    return True


def _autocommit(engine: sa.Engine) -> sa.Connection:
    return engine.connect().execution_options(isolation_level="AUTOCOMMIT")


# --- the key itself: deterministic, per-source, and out of the pricing ledger's way ----------------


def test_the_same_source_key_always_lands_on_the_same_advisory_key():
    """Two cron lines are two processes. A key derived from anything process-local (PYTHONHASHSEED
    salts `hash()`) would give them different keys and coordinate nothing."""
    assert advisory_key("oliveyoung") == (LOCK_CLASS, -1252964274)

    script = "from collectors.commerce.storage.locks import advisory_key; print(advisory_key('oliveyoung'))"
    seen = set()
    for seed in ("1", "2", "random"):
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO_ROOT)},
            check=True,
        )
        seen.add(out.stdout.strip())
    assert seen == {str((LOCK_CLASS, -1252964274))}


def test_every_registered_source_gets_a_key_of_its_own():
    keys = {key: advisory_key(key) for key in SOURCES}
    assert len(keys) == 4, keys
    assert len(set(keys.values())) == len(keys), f"two sources share one lock: {keys}"
    for classid, objid in keys.values():
        assert classid == LOCK_CLASS
        # pg_try_advisory_lock(int, int) takes int4; a wider number would raise, not lock.
        assert -(2**31) <= objid < 2**31


@pytest.mark.postgres
def test_the_source_lock_and_the_pricing_ledger_lock_cannot_collide(runtime_url_for_tests: str):
    """`analysis/polarity/pricing.py:89` holds `pg_advisory_xact_lock(6)`. That single-argument form
    is a different lock space from the two-argument one (Postgres tags them objsubid 1 vs 2), so even
    a key numerically identical to ours is a different lock -- shown here rather than assumed."""
    engine = sa.create_engine(runtime_url_for_tests)
    other = sa.create_engine(runtime_url_for_tests)
    try:
        with engine.begin() as ledger:
            ledger.execute(text("SELECT pg_advisory_xact_lock(6)"))
            with _autocommit(other) as observer:
                # The pathological case: the same two numbers, in the other form.
                assert observer.execute(text("SELECT pg_try_advisory_lock(0, 6)")).scalar_one() is True, (
                    "the two-argument lock space is not disjoint from the one-argument one"
                )
                observer.execute(text("SELECT pg_advisory_unlock(0, 6)"))
                for key in SOURCES:
                    assert _free(observer, key), f"the pricing ledger's lock blocks {key}"
    finally:
        engine.dispose()
        other.dispose()


# --- what a second run does when it cannot have a source ------------------------------------------


@pytest.mark.postgres
def test_a_source_another_run_holds_is_skipped_while_the_rest_are_walked(runtime_url_for_tests: str):
    holder_engine = sa.create_engine(runtime_url_for_tests)
    engine = sa.create_engine(runtime_url_for_tests)
    fetcher = _RecordingFetcher()
    try:
        with _autocommit(holder_engine) as holder:
            assert _take(holder, "locked")
            report = collect(
                sources=[_source("locked"), _source("free")],
                dataset=Dataset.RANKING,
                sink=_NullSink(),
                captured_at=AT,
                fetcher=fetcher,
                lock=PostgresSourceLock(engine),
            )
            _release(holder, "locked")

        assert report.skipped == ["locked"]
        assert report.sources["locked"].skipped_reason is not None
        assert report.sources["locked"].requests == 0
        assert not any("/locked/" in url for url in fetcher.urls), fetcher.urls

        # The other source is untouched by the one we yielded.
        assert report.sources["free"].requests == 1
        assert [u for u in fetcher.urls if "/free/" in u]

        assert not report.ok
        # 1, not 2: nobody refused us, we stood down.
        assert report.blocked == []
        assert exit_code_for(report) == 1
    finally:
        holder_engine.dispose()
        engine.dispose()


@pytest.mark.postgres
def test_the_next_run_takes_a_source_the_previous_one_skipped(runtime_url_for_tests: str):
    holder_engine = sa.create_engine(runtime_url_for_tests)
    engine = sa.create_engine(runtime_url_for_tests)
    lock = PostgresSourceLock(engine)
    try:
        with _autocommit(holder_engine) as holder:
            assert _take(holder, "locked")
            first = collect(
                sources=[_source("locked")],
                dataset=Dataset.RANKING,
                sink=_NullSink(),
                captured_at=AT,
                fetcher=_RecordingFetcher(),
                lock=lock,
            )
            assert first.skipped == ["locked"]
            _release(holder, "locked")

        fetcher = _RecordingFetcher()
        second = collect(
            sources=[_source("locked")],
            dataset=Dataset.RANKING,
            sink=_NullSink(),
            captured_at=AT,
            fetcher=fetcher,
            lock=lock,
        )
        assert second.skipped == []
        assert second.sources["locked"].requests == 1
        assert fetcher.urls
        assert exit_code_for(second) == 0
    finally:
        holder_engine.dispose()
        engine.dispose()


@pytest.mark.postgres
def test_the_lock_is_given_back_when_the_walk_ends(runtime_url_for_tests: str):
    engine = sa.create_engine(runtime_url_for_tests)
    observer_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with _autocommit(observer_engine) as observer:
            with PostgresSourceLock(engine)("oliveyoung") as held:
                assert held
                assert not _free(observer, "oliveyoung"), "the lock was never actually taken"
            assert _free(observer, "oliveyoung"), "the lock outlived the walk it was taken for"
    finally:
        engine.dispose()
        observer_engine.dispose()


# --- the two ways a held lock silently stops being held --------------------------------------------


@pytest.mark.postgres
def test_the_lock_dies_with_the_process_that_holds_it(runtime_url_for_tests: str):
    """Why session scope and not a row in a table: a run killed mid-walk (OOM, `docker kill`, a
    reboot) leaves nothing behind to clean up. Killing the backend is that crash, minus the process."""
    holder_engine = sa.create_engine(runtime_url_for_tests, poolclass=NullPool)
    observer_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with _autocommit(observer_engine) as observer:
            with PostgresSourceLock(holder_engine)("oliveyoung") as held:
                assert held
                rows = _granted(observer, "oliveyoung")
                assert len(rows) == 1, rows
                observer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": rows[0].pid})

                deadline = time.monotonic() + FREED_TIMEOUT_S
                while not _free(observer, "oliveyoung") and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert _free(observer, "oliveyoung"), "a dead holder kept the lock"
            # Leaving the block after the kill is cleanup, not a second failure to report.
            assert _free(observer, "oliveyoung")
    finally:
        holder_engine.dispose()
        observer_engine.dispose()


@pytest.mark.postgres
def test_the_lock_connection_never_sits_in_a_transaction_for_the_walk(runtime_url_for_tests: str):
    """The trap this unit is most likely to fall into. A walk runs 3.4 to 29 minutes and the runtime
    role sets `idle_in_transaction_session_timeout = 15s`: a lock taken inside a transaction that
    then waits on HTTP is a lock Postgres quietly takes back, and the run keeps walking as if it
    still had it. The timeouts here are the role's own, squeezed to sub-second."""
    holder_engine = sa.create_engine(
        runtime_url_for_tests, poolclass=NullPool, connect_args={"options": SQUEEZED_TIMEOUTS}
    )
    observer_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with holder_engine.connect() as probe:
            # Without this the timing below could pass on a connection that simply never got the
            # squeezed limits and had 15 real seconds to sit in.
            assert probe.execute(text(EFFECTIVE_IDLE_TIMEOUT)).scalar_one() == "200ms"

        with _autocommit(observer_engine) as observer:
            with PostgresSourceLock(holder_engine)("oliveyoung") as held:
                assert held
                rows = _granted(observer, "oliveyoung")
                assert len(rows) == 1, rows
                # `idle` is what dodges the two transaction timeouts -- and, read the other way, the
                # one state `idle_session_timeout` kills, so this line is a precondition of the test
                # below it (test_idle_session_timeout_takes_the_lock_away_and_the_walk_is_not_told)
                # rather than evidence against that failure mode.
                assert rows[0].state == "idle", (
                    f"the lock connection is {rows[0].state!r}; idle_in_transaction_session_timeout "
                    "will end this session, and the lock with it, partway through the walk"
                )

                time.sleep(IDLE_KILL_MARGIN_S)  # a walk, as far as this connection can tell

                assert len(_granted(observer, "oliveyoung")) == 1, (
                    "the lock's session was killed mid-walk; the run would have gone on walking a "
                    "source another run could now take too"
                )
                assert not _free(observer, "oliveyoung")
    finally:
        holder_engine.dispose()
        observer_engine.dispose()


# --- the wiring: the thing cron runs is the thing that takes the lock -------------------------------


@pytest.mark.postgres
def test_the_cli_skips_a_locked_source_and_records_why(trend_radar_schema: str, runtime_url_for_tests: str):
    """`product` is oliveyoung's alone, so a fetch of any kind here means the lock was not consulted.

    The other run connects as the runtime role: the CLI itself needs both of the migrator's two
    connections here (db/bootstrap.sql), its own pool and the one its lock holds.
    """
    # The CLI's pool and the lock it holds are one engine, so this run needs 1 + the widest concurrency
    # among the sources that declare `product` at once, and the role's own cap is two: a FATAL here
    # would look like a lock defect instead of the ceiling it is.
    wanted = 1 + max(cls.policy.concurrency for cls in SOURCES.values() if Dataset.PRODUCT in cls.datasets)
    cli_engine = sa.create_engine(trend_radar_schema)
    with cli_engine.connect() as conn:
        role, cap = conn.execute(
            text("SELECT current_user, rolconnlimit FROM pg_roles WHERE rolname = current_user")
        ).one()
    cli_engine.dispose()
    assert cap < 0 or wanted <= cap, (
        f"this test opens {wanted} connections as {role}, whose CONNECTION LIMIT is {cap}: raise the "
        f"limit or give the CLI a role with room, or the failure will read as a lock defect"
    )

    holder_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with _autocommit(holder_engine) as holder:
            assert _take(holder, "oliveyoung")
            code = cli.run(
                "product",
                database_url=trend_radar_schema,
                fetcher=_NeverFetcher(),
                captured_at=AT,
            )
            _release(holder, "oliveyoung")

        assert code == 1

        engine = sa.create_engine(trend_radar_schema)
        with engine.connect() as conn:
            run_row = conn.execute(sa.select(run_table.c.status, run_table.c.note)).one()
            source_row = conn.execute(
                sa.select(
                    run_source_table.c.outcome,
                    run_source_table.c.errors,
                    run_source_table.c.requests,
                ).where(run_source_table.c.source == "oliveyoung")
            ).one()
        engine.dispose()

        # P16's table reads `status` straight out of this column (needs.collector_health).
        assert run_row.status == "partial"
        assert "oliveyoung" in (run_row.note or "")
        assert source_row.outcome == "skipped"
        assert "lock" in (source_row.errors or "")
        assert source_row.requests == 0
    finally:
        holder_engine.dispose()


# --- the fourth timeout, the one AUTOCOMMIT does not dodge ------------------------------------------

IDLE_SESSION_SQUEEZE = "-c idle_session_timeout=200ms"
EFFECTIVE_IDLE_SESSION_TIMEOUT = "SELECT current_setting('idle_session_timeout')"


@pytest.mark.postgres
def test_the_runtime_role_leaves_idle_session_timeout_off(runtime_url_for_tests: str):
    """The lock connection sits `idle` -- not `idle in transaction` -- for the whole walk, and
    `idle_session_timeout` (PG14+) is the one limit that ends exactly that state, so AUTOCOMMIT is no
    defence against it. This design holds only while the value is 0.

    The role here is `needs_runtime`, the one db/bootstrap.sql creates; production commerce connects
    as `trend_radar_runtime`, which this repo does not create -- checked by hand in production on
    2026-08-24 (`pg_db_role_setting`, where `ALTER ROLE ... IN DATABASE` settings live) and unset
    there too.
    """
    engine = sa.create_engine(runtime_url_for_tests)
    try:
        with engine.connect() as conn:
            assert conn.execute(text(EFFECTIVE_IDLE_SESSION_TIMEOUT)).scalar_one() == "0", (
                "idle_session_timeout is set for the role the collectors connect as; the lock "
                "connection is idle for the length of the walk and Postgres will end it partway "
                "through -- see the test below for what that costs"
            )
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_idle_session_timeout_takes_the_lock_away_and_the_walk_is_not_told(
    runtime_url_for_tests: str, capsys: pytest.CaptureFixture[str]
):
    """What the guard above guards, run rather than argued: one `ALTER ROLE` and the lock is gone
    mid-walk while `held` is still True, which is the same silent double-rate the unit exists to
    prevent. Squeezed to sub-second so a test can outlive it."""
    holder_engine = sa.create_engine(
        runtime_url_for_tests, poolclass=NullPool, connect_args={"options": IDLE_SESSION_SQUEEZE}
    )
    observer_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with _autocommit(observer_engine) as observer:
            with PostgresSourceLock(holder_engine)("oliveyoung") as held:
                assert held
                rows = _granted(observer, "oliveyoung")
                assert len(rows) == 1 and rows[0].state == "idle", rows

                deadline = time.monotonic() + FREED_TIMEOUT_S
                while not _free(observer, "oliveyoung") and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert _free(observer, "oliveyoung"), "idle_session_timeout did not reach the session"
                # The point: nothing in the walk's hands changed when the lock went.
                assert held is True

            # Giving back a lock whose session is gone is the only place this is ever noticed, and it
            # has to read as the disconnect it is, not as the live-connection failure below.
            out = capsys.readouterr().out
            assert "oliveyoung" in out and "session was gone" in out, out
    finally:
        holder_engine.dispose()
        observer_engine.dispose()


# --- giving it back: the failures that leave the lock behind ----------------------------------------


@pytest.mark.postgres
def test_a_lock_that_was_already_lost_is_said_out_loud_when_it_is_given_back(
    runtime_url_for_tests: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """`pg_advisory_unlock` answering false means this session did not hold what it thought it held,
    and it is the only signal a walk ever gets: nothing re-checks between taking the lock and giving
    it back. `IS NULL` keeps the unlock real -- the source is genuinely released -- and forces only
    the answer, because a session cannot be made to lose a lock it is still alive to hold."""
    monkeypatch.setattr(locks, "GIVE_BACK", text("SELECT pg_advisory_unlock(:classid, :objid) IS NULL"))
    engine = sa.create_engine(runtime_url_for_tests)
    observer_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with _autocommit(observer_engine) as observer:
            with PostgresSourceLock(engine)("oliveyoung") as held:
                assert held
            assert _free(observer, "oliveyoung")
        out = capsys.readouterr().out
        assert "oliveyoung" in out and "did not hold it" in out, out
    finally:
        engine.dispose()
        observer_engine.dispose()


@pytest.mark.postgres
def test_an_unlock_that_fails_on_a_live_connection_leaves_the_lock_on_the_pooled_connection(
    runtime_url_for_tests: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """A swallowed unlock failure is not a lock that got released some other way. A session advisory
    lock survives the pool's ROLLBACK on check-in and the next checkout is the same backend, so the
    next source in the run is handed a connection still holding this source's lock -- until
    `engine.dispose()`. Division by zero fails the statement without ending the session, which is the
    case the disconnect path must not be confused with."""
    monkeypatch.setattr(locks, "GIVE_BACK", text("SELECT 1 / (:classid - :classid) + :objid"))
    engine = sa.create_engine(runtime_url_for_tests, pool_size=1, max_overflow=0)
    observer_engine = sa.create_engine(runtime_url_for_tests)
    try:
        with _autocommit(observer_engine) as observer:
            with PostgresSourceLock(engine)("oliveyoung") as held:
                assert held
            assert not _free(observer, "oliveyoung"), "the lock did not survive check-in after all"
            out = capsys.readouterr().out
            assert "oliveyoung" in out and "pool" in out, out

            engine.dispose()
            assert _free(observer, "oliveyoung")
    finally:
        engine.dispose()
        observer_engine.dispose()


# --- the wiring, part two: no second caller can walk without a lock ---------------------------------

ENGINE_MODULE = "collectors.commerce.engine"


def _collect_calls(source: str) -> list[tuple[int, bool]]:
    """Every call to `collectors.commerce.engine.collect` in `source`, as (line, passes a `lock=`).

    `collect()`'s `lock` defaults to `uncoordinated`, so a caller that forgets it collects at twice a
    site's declared rate and no test goes red. Whatever name the module bound `collect` to is followed
    here; a dotted `<anything>.engine.collect` counts too, which can only over-report."""
    tree = ast.parse(source)
    names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == ENGINE_MODULE
        for alias in node.names
        if alias.name == "collect"
    }
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = ast.unparse(node.func)
        if called in names or called.endswith("engine.collect"):
            calls.append((node.lineno, any(kw.arg == "lock" for kw in node.keywords)))
    return sorted(calls)


def _repo_python_files() -> list[Path]:
    """Every non-test Python file in the repo, asked of git so `.venv` and friends stay out of it.

    `--others --exclude-standard` as well as the index: a new caller is an untracked file until it is
    committed, and that is exactly when its author is running this suite."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.py"],
        capture_output=True,
        cwd=REPO_ROOT,
        check=True,
    )
    paths = [REPO_ROOT / p for p in out.stdout.decode().split("\0") if p]
    return [p for p in paths if not p.is_relative_to(REPO_ROOT / "tests")]


def test_every_caller_of_collect_outside_the_tests_passes_a_lock():
    """The structural half of the lock: `collect(lock=...)` is wired in one place today, and a second
    entrypoint (a backfill, another subcommand) that forgets it would be silent otherwise."""
    offenders = {
        str(path.relative_to(REPO_ROOT)): [line for line, has_lock in calls if not has_lock]
        for path in _repo_python_files()
        for calls in [_collect_calls(path.read_text(encoding="utf-8"))]
        if any(not has_lock for _, has_lock in calls)
    }
    assert not offenders, f"collect() called without lock=: {offenders}"


def test_the_caller_check_sees_the_cli_and_bites_a_caller_without_a_lock():
    """A scanner that matches nothing passes the test above forever. So: it finds the one real caller,
    and it fails a caller that drops the argument."""
    cli_calls = _collect_calls((REPO_ROOT / "collectors" / "commerce" / "cli.py").read_text("utf-8"))
    assert cli_calls and all(has_lock for _, has_lock in cli_calls), cli_calls

    forgetful = "from collectors.commerce.engine import collect\ncollect(sources=[], dataset=None)\n"
    assert _collect_calls(forgetful) == [(2, False)]
    assert _collect_calls(forgetful.replace("dataset=None", "dataset=None, lock=None")) == [(2, True)]
