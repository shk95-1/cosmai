"""#274: an artifact row without its payload file, and what one job's death does to the batch.

Production, 2026-09-19: 3,707 `video.transcript` artifact rows written by the old fleet are still
inside their freshness window and not one of their files is in the new stack's volume. `_collect_one`
read the cached payload outside the try that turns one job's failure into a `failed` row, so the
first such job raised `FileNotFoundError` out of `_run_work`, the transaction rolled back, every
claimed job stayed `queued` -- and the next five-minute tick met the same row and died the same way.

Why no test caught it: every other test in this package builds an artifact and its payload together,
so the store always has the file the row names. **The axis here is a row WITHOUT its file** -- the
"returns less" shape of #90 applied to our own store instead of to a source -- and it is produced the
way production produced it: the row is inserted and nothing is written under the digest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from collectors.youtube import queue, transport
from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts, jobs
from collectors.youtube.transport import DataApiClient, LiveFetcher, Route

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 9, 19, 20, tzinfo=UTC)
BROKEN = "vidbrokenpay"  # the video whose fresh artifact has no readable payload
NEIGHBOUR = "vidneighbour"  # the job that shared its batch and must be untouched
GONE_DIGEST = "0" * 64
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _SavedDumps:
    """Answers out of a dict the test wrote, never out of the request (#90). A target nobody saved an
    answer for is a fetch that found nothing, not an invented success."""

    def __init__(self, dumps: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._dumps = dict(dumps)
        self.asked: list[tuple[str, str]] = []

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.asked.append((spec.kind, spec.target))
        try:
            return self._dumps[(spec.kind, spec.target)]
        except KeyError:
            raise transport.Unavailable(f"nothing saved for {spec.target}", route=Route.DATA_API) from None


def _metadata_dumps(*video_ids: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        ("video.metadata", video_id): {"id": video_id, "title": f"a saved title for {video_id}"}
        for video_id in video_ids
    }


def _queue(schema: str, tmp_path: Path, *video_ids: str) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("".join(f"video {video_id}\n" for video_id in video_ids))
    assert run("watch", read_roster=False, database_url=schema, watchlist_path=watchlist, captured_at=T0) == 0


def _insert_fresh_artifact(schema: str, *, target: str, digest: str, fetched_at: datetime) -> None:
    """A `video.metadata` row that is still fresh at T0. Nothing is written under `digest` unless the
    test writes it -- that absence is what the whole file is about."""
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(artifacts).values(
                    identifier=f"old{target}"[:32],
                    kind="video.metadata",
                    target=target,
                    fingerprint=f"video.metadata:{target}",
                    digest=digest,
                    byte_count=2,
                    fetched_at=fetched_at,
                    fresh_until=T0 + timedelta(hours=1),
                    schema_version="1",
                    fetch_route=Route.DATA_API,
                )
            )
    finally:
        engine.dispose()


def _job_rows(schema: str) -> dict[str, tuple[str, str | None]]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            rows = conn.execute(sa.select(jobs.c.target, jobs.c.state, jobs.c.error_code)).all()
    finally:
        engine.dispose()
    return {row.target: (row.state, row.error_code) for row in rows}


def _artifact_digests(schema: str, target: str) -> list[str]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            return list(
                conn.execute(
                    sa.select(artifacts.c.digest)
                    .where(artifacts.c.target == target)
                    .order_by(artifacts.c.fetched_at)
                ).scalars()
            )
    finally:
        engine.dispose()


def _work(schema: str, tmp_path: Path, fetcher: Any, *, at: datetime = T0) -> int:
    """`work`, and a crash is reported as this test's own failure rather than as a traceback -- the
    defect is that one job's exception leaves the batch, so that is what has to be asserted on."""
    try:
        return run(
            "work",
            database_url=schema,
            fetcher=fetcher,
            payload_root=tmp_path / "payloads",
            captured_at=at,
        )
    except Exception as error:  # noqa: BLE001 - the crash IS the defect under test
        pytest.fail(f"one job's {type(error).__name__} left _run_work and took the batch with it: {error}")


# --- (a) a fresh artifact row with no payload file --------------------------------------------------


def test_a_fresh_artifact_with_no_payload_file_is_a_miss_and_the_job_fetches(
    tubedepth_schema: str, tmp_path: Path
):
    _queue(tubedepth_schema, tmp_path, BROKEN, NEIGHBOUR)
    _insert_fresh_artifact(
        tubedepth_schema, target=BROKEN, digest=GONE_DIGEST, fetched_at=T0 - timedelta(hours=1)
    )
    fetcher = _SavedDumps(_metadata_dumps(BROKEN, NEIGHBOUR))

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0

    assert ("video.metadata", BROKEN) in fetcher.asked, "an unreadable payload answers nothing"
    assert _job_rows(tubedepth_schema)[BROKEN] == ("succeeded", None)


def test_the_neighbour_in_the_same_batch_is_untouched_by_the_unreadable_row(
    tubedepth_schema: str, tmp_path: Path
):
    """The defect's real cost: the wedged job was one of 88, and the other 87 never ran. BROKEN is
    queued first, so it is claimed first and its failure is the batch's."""
    _queue(tubedepth_schema, tmp_path, BROKEN, NEIGHBOUR)
    _insert_fresh_artifact(
        tubedepth_schema, target=BROKEN, digest=GONE_DIGEST, fetched_at=T0 - timedelta(hours=1)
    )
    fetcher = _SavedDumps(_metadata_dumps(BROKEN, NEIGHBOUR))

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0

    rows = _job_rows(tubedepth_schema)
    assert rows[NEIGHBOUR] == ("succeeded", None)
    assert "queued" not in {state for state, _code in rows.values()}, "no job is left for the next tick"


def test_a_payload_file_that_is_not_json_is_a_miss_too(tubedepth_schema: str, tmp_path: Path):
    """The file being there is not the question -- being readable is. A half-written file is the same
    answer as no file, and `json.loads` raises a different exception for it."""
    payloads = PayloadStore(tmp_path / "payloads")
    path = payloads._path_for("video.metadata", GONE_DIGEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id": "vidbro', encoding="utf-8")

    _queue(tubedepth_schema, tmp_path, BROKEN)
    _insert_fresh_artifact(
        tubedepth_schema, target=BROKEN, digest=GONE_DIGEST, fetched_at=T0 - timedelta(hours=1)
    )
    fetcher = _SavedDumps(_metadata_dumps(BROKEN))

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0
    assert _job_rows(tubedepth_schema)[BROKEN] == ("succeeded", None)


def test_the_miss_does_not_repeat_on_the_next_pass(tubedepth_schema: str, tmp_path: Path):
    """Otherwise the fix trades a wedge for a leak: every five minutes, forever, one re-fetch per
    unreadable row. The re-fetch writes a second fresh row beside the unreadable one and
    `_fresh_artifact` has to hand back the newer of the two."""
    _queue(tubedepth_schema, tmp_path, BROKEN)
    _insert_fresh_artifact(
        tubedepth_schema, target=BROKEN, digest=GONE_DIGEST, fetched_at=T0 - timedelta(hours=1)
    )
    fetcher = _SavedDumps(_metadata_dumps(BROKEN))

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0
    assert len(fetcher.asked) == 1

    digests = _artifact_digests(tubedepth_schema, BROKEN)
    assert len(digests) == 2, "the re-fetch writes a row beside the unreadable one, not over it"
    assert digests[-1] != GONE_DIGEST

    _queue(tubedepth_schema, tmp_path, BROKEN)
    assert _work(tubedepth_schema, tmp_path, fetcher, at=T0 + timedelta(minutes=5)) == 0
    assert len(fetcher.asked) == 1, "the readable row is the newer one, so the next pass is a cache hit"


def test_the_miss_says_which_target_re_fetched_and_shows_no_payload(
    tubedepth_schema: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    _queue(tubedepth_schema, tmp_path, BROKEN)
    _insert_fresh_artifact(
        tubedepth_schema, target=BROKEN, digest=GONE_DIGEST, fetched_at=T0 - timedelta(hours=1)
    )
    assert _work(tubedepth_schema, tmp_path, _SavedDumps(_metadata_dumps(BROKEN))) == 0

    printed = capsys.readouterr().out
    line = next((one for one in printed.splitlines() if BROKEN in one and "video.metadata" in one), None)
    assert line is not None, f"the miss names its kind and target; printed: {printed!r}"
    assert "title" not in line, "a payload in a log line is a harvest in a log line"


# --- (b) an unexpected exception ends one job, not the batch ----------------------------------------


def test_an_unexpected_exception_fails_its_own_job_and_the_batch_carries_on(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A full disk, in the one step that sits outside the fetch's try: `payloads.put`. Nothing about
    the payload store is the source's fault, and before #274 it ended the run by traceback with the
    rest of the batch unattempted."""
    real_put = PayloadStore.put

    def refuse_to_store(self: PayloadStore, kind: str, payload: Any):
        if BROKEN in json.dumps(payload, default=str):
            raise OSError(28, "No space left on device")
        return real_put(self, kind, payload)

    monkeypatch.setattr(PayloadStore, "put", refuse_to_store)

    _queue(tubedepth_schema, tmp_path, BROKEN, NEIGHBOUR)
    fetcher = _SavedDumps(_metadata_dumps(BROKEN, NEIGHBOUR))

    # Partial, not blocked: our own disk is not the source refusing us.
    assert _work(tubedepth_schema, tmp_path, fetcher) == 1

    rows = _job_rows(tubedepth_schema)
    assert rows[BROKEN] == ("failed", "internal")
    assert rows[NEIGHBOUR] == ("succeeded", None)
    assert "running" not in {state for state, _code in rows.values()}


CHANNEL = "@a_channel"
LISTING_DUMP = {
    "id": "UUa_channel",
    "title": "Uploads",
    "entries": [{"id": "dQw4w9WgXcQ", "title": "t", "channel_id": "UUa_channel"}],
}


def _listing_job_states(schema: str) -> list[tuple[str, str | None]]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                sa.select(jobs.c.state, jobs.c.error_code).where(jobs.c.kind == "channel.videos")
            ).all()
    finally:
        engine.dispose()
    return sorted((row.state, row.error_code) for row in rows)


def test_a_job_that_dies_after_its_artifact_row_takes_that_row_with_it(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A failure between the artifact row and the job row, at a real seam: the follow-up fan-out,
    which runs after the insert. Without the savepoint the artifact row would outlive the job that
    says it failed -- and worse, `_fresh_artifact` would serve it, so the next pass would read a
    cache whose job never finished. The second listing job proves the batch went on.

    The watch line queues two listing jobs (one per follow-up kind), which is what gives this file a
    first job to kill and a second to watch."""
    calls = {"n": 0}
    real_fan_out = queue.fan_out_follow_up

    def die_once(*args: Any, **kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a shape nobody has seen")
        return real_fan_out(*args, **kwargs)

    monkeypatch.setattr(queue, "fan_out_follow_up", die_once)

    watchlist = tmp_path / "watch.txt"
    watchlist.write_text(f"channel {CHANNEL}\n")
    assert (
        run(
            "watch",
            read_roster=False,
            database_url=tubedepth_schema,
            watchlist_path=watchlist,
            captured_at=T0,
        )
        == 0
    )

    assert _work(tubedepth_schema, tmp_path, _SavedDumps({("channel.videos", CHANNEL): LISTING_DUMP})) == 1

    assert _listing_job_states(tubedepth_schema) == [("failed", "internal"), ("succeeded", None)]
    assert len(_artifact_digests(tubedepth_schema, CHANNEL)) == 1, (
        "the dead job's artifact row went back with it; the surviving job's stayed"
    )


# --- the batched metadata route still fetches a job that turned into a miss (#259) -------------------


def _live_fetcher(responses: list[tuple[int, dict[str, Any]]]) -> tuple[LiveFetcher, list[httpx.Request]]:
    """The #259 fake: saved bodies replayed in order, never built from the request."""
    seen: list[httpx.Request] = []
    remaining = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = remaining.pop(0) if remaining else responses[-1]
        return httpx.Response(status, json=body)

    budget = transport.RequestBudget({Route.DATA_API: 200}, {Route.DATA_API: 0.0}, sleep=lambda _s: None)
    client = DataApiClient("dummy-data-api-key", transport=httpx.MockTransport(handle), budget=budget)
    return LiveFetcher(data_api=client, metadata_route=Route.DATA_API, budget=budget), seen


def _videos_list_body(*video_ids: str) -> dict[str, Any]:
    item = json.loads((FIXTURES / "data_api" / "videos-list.json").read_text(encoding="utf-8"))["items"][0]
    return {
        "kind": "youtube#videoListResponse",
        "pageInfo": {"totalResults": len(video_ids), "resultsPerPage": len(video_ids)},
        "items": [dict(item, id=video_id) for video_id in video_ids],
    }


def test_a_prefetch_skipped_target_that_turns_into_a_miss_is_still_fetched(
    tubedepth_schema: str, tmp_path: Path
):
    """`_prefetch` leaves out every target a fresh artifact answers, so the unreadable one is not in
    the batched `videos.list` call. When it then turns out to answer nothing, `LiveFetcher.fetch`
    finds no primed dump and must make the one call for it -- not raise a KeyError over the dict
    #259 primes."""
    _queue(tubedepth_schema, tmp_path, BROKEN, NEIGHBOUR)
    _insert_fresh_artifact(
        tubedepth_schema, target=BROKEN, digest=GONE_DIGEST, fetched_at=T0 - timedelta(hours=1)
    )
    fetcher, seen = _live_fetcher([(200, _videos_list_body(NEIGHBOUR)), (200, _videos_list_body(BROKEN))])

    assert _work(tubedepth_schema, tmp_path, fetcher) == 0

    asked = [request.url.params["id"].split(",") for request in seen]
    assert asked == [[NEIGHBOUR], [BROKEN]], "the batch call skips the fresh row; the miss asks for itself"
    rows = _job_rows(tubedepth_schema)
    assert rows[BROKEN] == ("succeeded", None)
    assert rows[NEIGHBOUR] == ("succeeded", None)
