"""#277: `work` held one transaction open across a whole batch's network fetches.

Production, 2026-09-19: `collectors/youtube/cli.py`'s `run` opened one transaction for the whole
invocation, so the claim, the batched `videos.list` prefetch, up to 50 network fetches and every
write lived inside it. The role the collector runs as is configured against the opposite shape --
`transaction_timeout=60s` and `idle_in_transaction_session_timeout=30s` on `tubedepth_runtime`, the
family `db/bootstrap_source.sql` sets -- and a connection is exactly *idle in transaction* while
yt-dlp or timedtext is on the network. Every pass that met a slow kind died at 60s with
`psycopg.errors.TransactionTimeout`, the whole batch rolled back, and the transcripts already
fetched were lost.

Why no test caught it: the fakes answer instantly, so wall-clock time under the role's real
timeouts was an axis no test had. **That axis is what this file adds.** The timeouts are real, set
on the session the way the role sets them on the connection, and the fake sleeps past them -- so a
transaction held across a fetch is killed by the server here exactly as it was in production, and
nothing about the assertion is a proxy for that.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from collectors.youtube import cli, transport
from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.models import ROUTES, JobState
from collectors.youtube.storage import db as storage_db
from collectors.youtube.storage.tables import artifacts, jobs
from collectors.youtube.transport import Route

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 9, 19, 20, tzinfo=UTC)
SLOW = "vidslowfetch"
NEIGHBOUR = "vidneighbour"
THIRD = "vidthirdjob"

#: The role's own family, two orders of magnitude smaller so a test can meet it within a few
#: seconds. Production carries 60s/30s; what matters is that the fetch outlasts them, not the size.
ROLE_TIMEOUT = "2s"
SLOW_FETCH_SECONDS = 2.5

#: `storage/db.py`'s `create_engine` stamps this on every connection `run()` opens, which is what
#: lets a test ask the server which of *this collector's* backends hold a transaction.
APPLICATION_NAME = "cosmai-youtube"


def _under_the_roles_timeouts(url: str) -> str:
    """The same database and schema, connected with the collector role's timeout family set on the
    session. `ALTER ROLE ... IN DATABASE` would say the same thing and would reach outside this
    test's own schema into every other test sharing the harness; `options` is the same server
    behaviour scoped to the one connection `run()` opens."""
    parsed = make_url(url)
    options = str(parsed.query.get("options", ""))
    timeouts = f"-ctransaction_timeout={ROLE_TIMEOUT} -cidle_in_transaction_session_timeout={ROLE_TIMEOUT}"
    return parsed.update_query_dict({"options": f"{options} {timeouts}".strip()}).render_as_string(
        hide_password=False
    )


def _metadata_dumps(*video_ids: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        ("video.metadata", video_id): {"id": video_id, "title": f"a saved title for {video_id}"}
        for video_id in video_ids
    }


class _SlowDumps:
    """Answers out of a dict the test wrote, after sleeping past the session's timeouts. The sleep
    is the fetch: a route on the network is a connection sitting idle in whatever transaction the
    caller left open."""

    def __init__(self, dumps: dict[tuple[str, str], dict[str, Any]], *, seconds: float) -> None:
        self._dumps = dict(dumps)
        self._seconds = seconds
        self.asked: list[tuple[str, str]] = []

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.asked.append((spec.kind, spec.target))
        time.sleep(self._seconds)
        try:
            return self._dumps[(spec.kind, spec.target)]
        except KeyError:
            raise transport.Unavailable(f"nothing saved for {spec.target}", route=Route.DATA_API) from None


#: Every backend of this collector's that holds an open transaction **on this test's own schema**.
#:
#: `application_name` alone is cluster-wide, and under `-n 4` it also answers for whatever another
#: worker's youtube test is doing: `flatten` runs one transaction per batch with a SAVEPOINT per
#: artifact and reads a payload file from disk inside it, so a backend of somebody else's sits idle
#: in transaction for seconds at a time. Measured 2026-09-19 on main at 0a6fcbe, before #272 was
#: written: this file beside test_youtube_pg_load.py and test_youtube_flatten_cursor.py under `-n 3`
#: failed 1 run in 5 that way, and the whole suite failed it on some orderings and not others (#272).
#:
#: `pg_locks` is what narrows it, because a relation oid belongs to one schema and each test has its
#: own: a transaction that claimed a job holds a lock on *this* schema's `jobs`, and another
#: worker's pass holds locks on its own. The defect under test is a transaction held across the
#: fetch *after* the claim, so it always holds one; the test below proves this query can still see
#: one rather than leaving that to the flag.
_OPEN_TRANSACTIONS = """
SELECT DISTINCT activity.state
FROM pg_stat_activity activity
JOIN pg_locks lock_row ON lock_row.pid = activity.pid
JOIN pg_class relation ON relation.oid = lock_row.relation
JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
WHERE activity.application_name = :app
  AND activity.xact_start IS NOT NULL
  AND namespace.nspname = :schema
"""


def _open_transactions(probe_url: str, schema: str) -> list[str]:
    engine = sa.create_engine(probe_url)
    try:
        with engine.connect() as conn:
            return list(
                conn.execute(
                    sa.text(_OPEN_TRANSACTIONS), {"app": APPLICATION_NAME, "schema": schema}
                ).scalars()
            )
    finally:
        engine.dispose()


class _AsksWhatTheServerSees:
    """Instant, but it asks `pg_stat_activity` from a connection of its own at the moment a route
    would be on the network -- both in `prefetch` (#259's batched `videos.list`) and in `fetch`."""

    def __init__(self, dumps: dict[tuple[str, str], dict[str, Any]], *, probe_url: str, schema: str) -> None:
        self._dumps = dict(dumps)
        self._probe_url = probe_url
        self._schema = schema
        self.open_transactions: list[list[str]] = []

    def _probe(self) -> None:
        self.open_transactions.append(_open_transactions(self._probe_url, self._schema))

    def prefetch(self, specs: list[FetchSpec]) -> None:
        del specs
        self._probe()

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self._probe()
        try:
            return self._dumps[(spec.kind, spec.target)]
        except KeyError:
            raise transport.Unavailable(f"nothing saved for {spec.target}", route=Route.DATA_API) from None


class _DiesOnTheSecondJob:
    """A worker killed mid-batch. `KeyboardInterrupt` is a `BaseException`, so it goes past the
    per-job guard the way a signal does rather than ending one job `failed`."""

    def __init__(self, dumps: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._dumps = dict(dumps)
        self.asked: list[tuple[str, str]] = []

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.asked.append((spec.kind, spec.target))
        if len(self.asked) == 2:
            raise KeyboardInterrupt("the worker was killed mid-batch")
        return self._dumps[(spec.kind, spec.target)]


def _queue(schema: str, tmp_path: Path, *video_ids: str) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("".join(f"video {video_id}\n" for video_id in video_ids))
    assert run("watch", read_roster=False, database_url=schema, watchlist_path=watchlist, captured_at=T0) == 0


def _work(schema: str, tmp_path: Path, fetcher: Any, *, at: datetime = T0) -> int:
    """`work`, with a crash reported as this test's own failure rather than as a traceback -- the
    defect is the server ending the transaction under the collector, so that is what is asserted on."""
    try:
        return run(
            "work",
            database_url=schema,
            fetcher=fetcher,
            payload_root=tmp_path / "payloads",
            captured_at=at,
        )
    except Exception as error:  # noqa: BLE001 - the killed transaction IS the defect under test
        pytest.fail(f"the pass died of {type(error).__name__} instead of finishing: {error}")


def _job_rows(schema: str) -> dict[str, tuple[str, str | None]]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            rows = conn.execute(sa.select(jobs.c.target, jobs.c.state, jobs.c.error_code)).all()
    finally:
        engine.dispose()
    return {row.target: (row.state, row.error_code) for row in rows}


def _artifact_targets(schema: str) -> list[str]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            return sorted(conn.execute(sa.select(artifacts.c.target)).scalars())
    finally:
        engine.dispose()


def _force_running(schema: str, target: str, *, started_at: datetime) -> None:
    """A claim that was committed and whose worker never came back -- what a killed process leaves
    behind once the claim is a transaction of its own."""
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.target == target)
                .values(state=JobState.RUNNING.value, started_at=started_at)
            )
    finally:
        engine.dispose()


# --- no transaction is open while the collector is on the network -----------------------------------


def test_a_pass_whose_fetches_outlast_the_roles_timeouts_finishes_and_keeps_what_it_fetched(
    tubedepth_schema: str, tmp_path: Path
):
    """The issue, under the server that killed it. Two fetches of 2.5s each against a 2s
    transaction_timeout: held across the batch, the second statement after the first sleep meets a
    terminated backend."""
    _queue(tubedepth_schema, tmp_path, SLOW, NEIGHBOUR)
    fetcher = _SlowDumps(_metadata_dumps(SLOW, NEIGHBOUR), seconds=SLOW_FETCH_SECONDS)

    assert _work(_under_the_roles_timeouts(tubedepth_schema), tmp_path, fetcher) == 0

    assert sorted(target for _kind, target in fetcher.asked) == sorted([SLOW, NEIGHBOUR])
    rows = _job_rows(tubedepth_schema)
    assert rows[SLOW] == ("succeeded", None)
    assert rows[NEIGHBOUR] == ("succeeded", None)
    assert _artifact_targets(tubedepth_schema) == sorted([SLOW, NEIGHBOUR]), (
        "what the pass fetched is kept -- the rows, not only the payload files"
    )


def test_one_slow_job_does_not_cost_the_batch_the_jobs_behind_it(tubedepth_schema: str, tmp_path: Path):
    """The cost the issue measured: a pass reached ~5 transcripts and the rest of the batch went
    back to `queued` to be fetched again five minutes later, forever."""
    _queue(tubedepth_schema, tmp_path, SLOW, NEIGHBOUR, THIRD)
    fetcher = _SlowDumps(_metadata_dumps(SLOW, NEIGHBOUR, THIRD), seconds=SLOW_FETCH_SECONDS)

    assert _work(_under_the_roles_timeouts(tubedepth_schema), tmp_path, fetcher) == 0

    rows = _job_rows(tubedepth_schema)
    assert sorted(rows) == sorted([SLOW, NEIGHBOUR, THIRD])
    assert all(state == "succeeded" for state, _code in rows.values()), rows


def test_the_probe_still_sees_a_transaction_this_schema_does_hold(tubedepth_schema: str, _schema_name: str):
    """What makes the empty answer below mean anything (#272). The probe is scoped to one schema so
    that another worker's pass is not read as this collector's, and a scope that had gone too narrow
    would answer "no transaction is open" to every question, including the one this file exists to
    ask."""
    engine = storage_db.create_engine(tubedepth_schema)
    try:
        with engine.begin() as conn:
            conn.execute(sa.select(sa.func.count()).select_from(jobs))
            assert _open_transactions(tubedepth_schema, _schema_name) == ["idle in transaction"]
    finally:
        engine.dispose()


def test_the_server_sees_no_transaction_of_this_collectors_while_a_route_is_on_the_network(
    tubedepth_schema: str, _schema_name: str, tmp_path: Path
):
    """Asked of `pg_stat_activity` rather than of the code: at the moment `prefetch` (#259's batched
    `videos.list`) and `fetch` are called, no backend of this collector's holds a transaction."""
    _queue(tubedepth_schema, tmp_path, SLOW, NEIGHBOUR)
    fetcher = _AsksWhatTheServerSees(
        _metadata_dumps(SLOW, NEIGHBOUR), probe_url=tubedepth_schema, schema=_schema_name
    )

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0

    assert len(fetcher.open_transactions) == 3, "one probe for the prefetch, one per job"
    assert fetcher.open_transactions == [[], [], []], (
        f"a transaction was open while the collector was on the network: {fetcher.open_transactions}"
    )


# --- a committed claim has a way back ---------------------------------------------------------------


def test_a_claim_left_running_by_a_dead_worker_is_taken_back_by_the_next_pass(
    tubedepth_schema: str, tmp_path: Path
):
    """Once the claim commits, a worker that dies leaves its jobs `running` and nothing else is
    coming for them. The next pass reclaims a claim older than the bound before it claims."""
    _queue(tubedepth_schema, tmp_path, SLOW)
    _force_running(tubedepth_schema, SLOW, started_at=T0 - timedelta(days=1))
    fetcher = _SlowDumps(_metadata_dumps(SLOW), seconds=0.0)

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0

    assert fetcher.asked == [("video.metadata", SLOW)]
    assert _job_rows(tubedepth_schema)[SLOW] == ("succeeded", None)


def test_a_claim_younger_than_the_bound_is_left_to_the_worker_that_holds_it(
    tubedepth_schema: str, tmp_path: Path
):
    """The other half: two `work` processes overlap by design (a 5-minute cron against a pass that
    can run longer), and reclaiming a job a live worker is still fetching would spend the request
    twice."""
    _queue(tubedepth_schema, tmp_path, SLOW)
    _force_running(tubedepth_schema, SLOW, started_at=T0 - timedelta(minutes=1))
    fetcher = _SlowDumps(_metadata_dumps(SLOW), seconds=0.0)

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0

    assert fetcher.asked == [], "a claim inside the bound belongs to whoever took it"
    assert _job_rows(tubedepth_schema)[SLOW][0] == "running"


def test_a_worker_that_dies_mid_batch_leaves_no_job_running_and_keeps_the_ones_it_finished(
    tubedepth_schema: str, tmp_path: Path
):
    """A signal, not an exception one job can own. What the process claimed and did not finish goes
    back to `queued` on the way out; what it did finish is already committed and stays."""
    _queue(tubedepth_schema, tmp_path, SLOW, NEIGHBOUR, THIRD)
    fetcher = _DiesOnTheSecondJob(_metadata_dumps(SLOW, NEIGHBOUR, THIRD))

    with pytest.raises(KeyboardInterrupt):
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=fetcher,
            payload_root=tmp_path / "payloads",
            captured_at=T0,
        )

    states = sorted(state for state, _code in _job_rows(tubedepth_schema).values())
    assert states == ["queued", "queued", "succeeded"], (
        f"the finished job is kept and the unfinished claims go back; got {states}"
    )


def test_the_bound_on_a_claim_outlasts_the_slowest_pass_that_is_merely_slow():
    """A bound shorter than a pass can legitimately be would reclaim jobs from a live worker. The
    slowest bounded pass is a full batch on the slowest route: `DEFAULT_WORK_BATCH` jobs at that
    route's own per-request timeout plus its pause. yt-dlp's comment extraction pages inside one
    request and has no wall-clock bound at all, so the real bound has to sit clear of that number,
    not on it."""
    slowest = max(float(route["timeout_s"]) + float(route["sleep_seconds"]) for route in ROUTES.values())
    merely_slow = timedelta(seconds=cli.DEFAULT_WORK_BATCH * slowest)

    bound = getattr(cli, "STALE_CLAIM_AFTER", None)
    assert bound is not None, "a committed claim a dead worker left behind has no way back at all"
    assert bound > merely_slow, (
        f"{bound} would reclaim a live worker's batch: a full batch on the slowest route is "
        f"already {merely_slow}"
    )
