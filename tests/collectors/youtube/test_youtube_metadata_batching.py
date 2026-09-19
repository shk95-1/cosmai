"""#259: `videos.list` carries up to 50 ids in one call, and what the other 49 do when that one
call goes wrong.

The defect this file holds shut is invisible at any scale a test runs at -- one call per video and
fifty videos in one call both return the same rows -- so every assertion here is on the **request
shape**: how many calls went out and what `id` each one carried. The cost is the reason: a panel
pass of 13,979 videos is 280 requests batched and 13,979 unbatched, against a Data API ceiling of
10,000 units a day.

**The fake never builds its answer out of the request.** It replays saved bodies in order, so a body
can carry fewer videos than the call asked for -- which is exactly what a deleted or private video
looks like, and exactly what #90's request-shaped fake could not show (the suite was green and
production wrote 0 rows). One test asks for three ids and is answered with two on purpose.

The three ways one call can go wrong, and where each leaves fifty jobs:

    a short response   the missing id's job fails `unavailable` -- where a single-id miss ends
                       today -- and its neighbours in the same call succeed.
    403 quotaExceeded  one job fails `quota` (a blocked code, exit 2) and the rest of the claimed
                       batch goes back to `queued`, which is what `work` already does with a block.
    500                every job in the call fails `http_500`; the run is partial, nothing is
                       blocked, and no job is left running or silently succeeded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from collectors.youtube import quota, transport
from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.storage.tables import artifacts, jobs
from collectors.youtube.transport import DataApiClient, LiveFetcher, Route

#: Only the run-level block below needs a database; the request-shape block is a transport and a
#: saved body, and marking it would cost it a container it never touches.
postgres = pytest.mark.postgres

API_KEY = "dummy-data-api-key"
T0 = datetime(2026, 9, 19, 20, tzinfo=UTC)  # 13:00 Pacific -- the middle of a quota day
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_ITEM = json.loads((FIXTURES / "data_api" / "videos-list.json").read_text(encoding="utf-8"))["items"][0]

QUOTA_403 = json.loads((FIXTURES / "data_api" / "error-403-quota-exceeded.json").read_text(encoding="utf-8"))

VIDEO_IDS = ("dQw4w9WgXcQ", "9bZkp7q19f0", "kJQP7kiw5Fk")


def _body(*video_ids: str) -> dict[str, Any]:
    """A saved `videos.list` body holding exactly these videos -- written by the test, not derived
    from any request. A body naming fewer videos than a call asked for is a real answer."""
    return {
        "kind": "youtube#videoListResponse",
        "pageInfo": {"totalResults": len(video_ids), "resultsPerPage": len(video_ids)},
        "items": [dict(VIDEO_ITEM, id=video_id) for video_id in video_ids],
    }


def _handler(*responses: tuple[int, dict[str, Any]]):
    """Answers each request with the next saved response and records what was asked. Keyed by
    nothing: the sequence is the fixture, so the answer cannot take its size from the question."""
    seen: list[httpx.Request] = []
    remaining = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = remaining.pop(0) if remaining else responses[-1]
        return httpx.Response(status, json=body)

    return handle, seen


def _client(handle: Any, *, budget: int = 200) -> DataApiClient:
    return DataApiClient(
        API_KEY,
        transport=httpx.MockTransport(handle),
        budget=transport.RequestBudget(
            {Route.DATA_API: budget}, {Route.DATA_API: 0.0}, sleep=lambda _s: None
        ),
    )


def _ids_of(request: httpx.Request) -> list[str]:
    return request.url.params["id"].split(",")


# --- the request shape ----------------------------------------------------------------------------


def test_n_ids_go_out_as_ceil_n_over_50_calls_of_at_most_50_comma_joined_ids():
    """The whole issue in one assertion. 120 videos are 3 calls and 3 quota units, not 120."""
    video_ids = [f"video{index:06d}" for index in range(120)]
    handle, seen = _handler(
        (200, _body(*video_ids[:50])),
        (200, _body(*video_ids[50:100])),
        (200, _body(*video_ids[100:])),
    )

    outcomes = _client(handle).video_metadata_many(video_ids)

    assert len(seen) == 3, "120 ids at 50 a call is 3 calls"
    sent = [_ids_of(request) for request in seen]
    assert [len(chunk) for chunk in sent] == [50, 50, 20]
    assert [video_id for chunk in sent for video_id in chunk] == video_ids
    assert set(outcomes) == set(video_ids)
    assert all(isinstance(outcome, dict) for outcome in outcomes.values())


def test_one_call_still_asks_for_every_part_because_a_call_costs_one_unit_whatever_is_in_it():
    handle, seen = _handler((200, _body(*VIDEO_IDS)))
    _client(handle).video_metadata_many(VIDEO_IDS)
    assert len(seen) == 1
    assert transport.OWNER_ONLY_PART in seen[0].url.params["part"]


def test_a_repeated_id_is_asked_for_once():
    handle, seen = _handler((200, _body("dQw4w9WgXcQ")))
    outcomes = _client(handle).video_metadata_many(["dQw4w9WgXcQ", "dQw4w9WgXcQ"])
    assert len(seen) == 1
    assert _ids_of(seen[0]) == ["dQw4w9WgXcQ"]
    assert set(outcomes) == {"dQw4w9WgXcQ"}


def test_an_id_the_response_left_out_is_unavailable_and_its_neighbours_are_not():
    """Three asked for, two answered. The missing one must end where a single-id miss ends today --
    `Unavailable` -- and must not take the two that did answer down with it."""
    handle, seen = _handler((200, _body(VIDEO_IDS[0], VIDEO_IDS[2])))

    outcomes = _client(handle).video_metadata_many(VIDEO_IDS)

    assert len(seen) == 1
    assert isinstance(outcomes[VIDEO_IDS[1]], transport.Unavailable)
    answered = [outcomes[VIDEO_IDS[0]], outcomes[VIDEO_IDS[2]]]
    assert all(isinstance(outcome, dict) for outcome in answered)
    assert [outcome["id"] for outcome in answered if isinstance(outcome, dict)] == [
        VIDEO_IDS[0],
        VIDEO_IDS[2],
    ]

    # The same class a single-id call raises, so `cli._classify_error` cannot read the two apart.
    single_handle, _ = _handler((200, _body()))
    with pytest.raises(transport.Unavailable):
        _client(single_handle).video_metadata(VIDEO_IDS[1])


def test_a_call_that_is_refused_the_owner_only_part_retries_that_call_for_all_of_its_ids():
    part_forbidden = json.loads(
        (FIXTURES / "data_api" / "error-403-part-forbidden.json").read_text(encoding="utf-8")
    )
    handle, seen = _handler((403, part_forbidden), (200, _body(*VIDEO_IDS)))

    client = _client(handle)
    outcomes = client.video_metadata_many(VIDEO_IDS)

    assert transport.OWNER_ONLY_PART in seen[0].url.params["part"]
    assert transport.OWNER_ONLY_PART not in seen[1].url.params["part"]
    assert _ids_of(seen[1]) == list(VIDEO_IDS)
    assert all(isinstance(outcome, dict) for outcome in outcomes.values())
    # The field is null on every row that call produced, so every one of them is counted.
    assert client.part_drops == list(VIDEO_IDS)


def test_a_block_stops_the_batch_rather_than_spending_a_request_on_every_remaining_chunk():
    """A 403 is the source refusing us; the next call would be refused the same way. Every id still
    gets the error, so no job is left without an answer."""
    video_ids = [f"video{index:06d}" for index in range(120)]
    handle, seen = _handler((403, QUOTA_403))

    outcomes = _client(handle).video_metadata_many(video_ids)

    assert len(seen) == 1
    assert set(outcomes) == set(video_ids)
    assert all(isinstance(outcome, transport.Blocked) for outcome in outcomes.values())


# --- what one call's failure does to fifty jobs ----------------------------------------------------


def _fetcher(handle: Any) -> LiveFetcher:
    return LiveFetcher(
        data_api=_client(handle),
        metadata_route=Route.DATA_API,
        budget=transport.RequestBudget({Route.DATA_API: 200}, {Route.DATA_API: 0.0}, sleep=lambda _s: None),
    )


def _queue_videos(schema: str, tmp_path: Path, video_ids=VIDEO_IDS) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("".join(f"video {video_id}\n" for video_id in video_ids))
    assert (
        run(
            "watch",
            read_roster=False,
            database_url=schema,
            watchlist_path=watchlist,
            captured_at=T0,
        )
        == 0
    )


def _job_rows(schema: str) -> dict[str, tuple[str, str | None]]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            rows = conn.execute(sa.select(jobs.c.target, jobs.c.state, jobs.c.error_code)).all()
    finally:
        engine.dispose()
    return {row.target: (row.state, row.error_code) for row in rows}


def _work(schema: str, tmp_path: Path, handle: Any) -> int:
    return run(
        "work",
        database_url=schema,
        fetcher=_fetcher(handle),
        payload_root=tmp_path / "payloads",
        captured_at=T0,
    )


@postgres
def test_three_metadata_jobs_are_one_request(tubedepth_schema: str, tmp_path: Path):
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((200, _body(*VIDEO_IDS)))

    assert _work(tubedepth_schema, tmp_path, handle) == 0

    assert len(seen) == 1, "one videos.list call carries every claimed video.metadata job"
    assert sorted(_ids_of(seen[0])) == sorted(VIDEO_IDS)
    assert all(state == "succeeded" for state, _code in _job_rows(tubedepth_schema).values())


@postgres
def test_a_video_missing_from_the_response_fails_alone(tubedepth_schema: str, tmp_path: Path):
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((200, _body(VIDEO_IDS[0], VIDEO_IDS[2])))

    # Partial, not blocked: one job failed and two wrote their rows.
    assert _work(tubedepth_schema, tmp_path, handle) == 1

    assert len(seen) == 1
    rows = _job_rows(tubedepth_schema)
    assert rows[VIDEO_IDS[1]] == ("failed", "unavailable")
    assert rows[VIDEO_IDS[0]] == ("succeeded", None)
    assert rows[VIDEO_IDS[2]] == ("succeeded", None)


@postgres
def test_a_403_quota_exceeded_on_the_batch_blocks_and_requeues_the_rest(
    tubedepth_schema: str, tmp_path: Path
):
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((403, QUOTA_403))

    assert _work(tubedepth_schema, tmp_path, handle) == 2

    assert len(seen) == 1
    rows = _job_rows(tubedepth_schema)
    failed = [target for target, (state, code) in rows.items() if (state, code) == ("failed", "quota")]
    queued = [target for target, (state, _code) in rows.items() if state == "queued"]
    assert len(failed) == 1, "the job that met the block is the one that carries it"
    assert sorted(failed + queued) == sorted(VIDEO_IDS), "no job is left running or half-finished"


@postgres
def test_a_500_on_the_batch_fails_every_job_in_it_and_blocks_nothing(tubedepth_schema: str, tmp_path: Path):
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((500, {"error": {"code": 500, "errors": [{"reason": "backendError"}]}}))

    assert _work(tubedepth_schema, tmp_path, handle) == 1

    assert len(seen) == 1
    assert _job_rows(tubedepth_schema) == dict.fromkeys(VIDEO_IDS, ("failed", "http_500"))


@postgres
def test_a_target_a_fresh_artifact_answers_is_left_out_of_the_call(tubedepth_schema: str, tmp_path: Path):
    """The batch spends its 50 slots on videos that need a fetch. A cached one is answered from
    `artifacts.fresh_until` in `_collect_one`, as it always was."""
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((200, _body(*VIDEO_IDS)))
    assert _work(tubedepth_schema, tmp_path, handle) == 0

    # Queue the same three again inside the freshness window: nothing should go out at all.
    _queue_videos(tubedepth_schema, tmp_path)
    again_handle, again_seen = _handler((200, _body(*VIDEO_IDS)))
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_fetcher(again_handle),
            payload_root=tmp_path / "payloads",
            captured_at=T0 + timedelta(minutes=5),
        )
        == 0
    )
    assert again_seen == [], "a fresh artifact answers, so no id belongs in a call"
    assert len(seen) == 1


# --- the day's bound ------------------------------------------------------------------------------


def _artifact(kind: str, target: str, *, fetched_at: datetime, route: str | None = Route.DATA_API):
    return {
        "identifier": f"{kind}:{target}:{fetched_at.isoformat()}"[:32],
        "kind": kind,
        "target": target,
        "fingerprint": f"{kind}:{target}",
        "digest": "0" * 64,
        "byte_count": 1,
        "fetched_at": fetched_at,
        "fresh_until": fetched_at,
        "schema_version": "1",
        "fetch_route": route,
    }


def _insert_artifacts(schema: str, rows: list[dict[str, Any]]) -> None:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(artifacts), rows)
    finally:
        engine.dispose()


def _spent_today(schema: str) -> int:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            return quota.requests_spent_today(conn, now=T0)
    finally:
        engine.dispose()


@postgres
def test_the_days_metadata_artifacts_count_as_the_calls_they_rode_on(
    tubedepth_schema: str,
):
    rows = [
        _artifact("video.metadata", f"video{index:06d}", fetched_at=T0 - timedelta(hours=1))
        for index in range(60)
    ]
    _insert_artifacts(tubedepth_schema, rows)
    # 60 videos at 50 an id list is 2 calls, not 60.
    assert _spent_today(tubedepth_schema) == 2


@postgres
def test_a_listing_walk_costs_its_pages_not_one_request(tubedepth_schema: str):
    _insert_artifacts(
        tubedepth_schema,
        [_artifact("channel.videos", "UC1", fetched_at=T0 - timedelta(hours=2))],
    )
    assert _spent_today(tubedepth_schema) == quota.LISTING_REQUESTS_PER_WALK


@postgres
def test_yesterdays_spend_and_another_route_are_not_this_days(tubedepth_schema: str):
    before_midnight = quota.day_start(T0) - timedelta(minutes=1)
    _insert_artifacts(
        tubedepth_schema,
        [
            _artifact("channel.videos", "UC1", fetched_at=before_midnight),
            _artifact("video.comments", "dQw4w9WgXcQ", fetched_at=T0, route=Route.YTDLP),
        ],
    )
    assert _spent_today(tubedepth_schema) == 0


def test_the_quota_day_is_googles_not_utc():
    """Google resets the Data API quota at midnight Pacific. A UTC day would put the boundary nine
    hours away from where the quota actually turns over."""
    start = quota.day_start(datetime(2026, 9, 19, 20, tzinfo=UTC))
    assert start.tzinfo is quota.QUOTA_RESET_ZONE
    assert (start.year, start.month, start.day, start.hour) == (2026, 9, 19, 0)
    assert start == datetime(2026, 9, 19, 7, tzinfo=UTC)  # PDT is UTC-7


@postgres
def test_a_day_already_spent_stops_the_route_in_a_later_process(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The bound is read from the database at the start of every run, so a `work` that starts with a
    fresh process and a fresh budget still sees what the runs before it spent -- which is the whole
    point: `work` is a cron invocation every five minutes."""
    monkeypatch.setattr(quota, "MAX_REQUESTS_PER_DAY", 1)
    _insert_artifacts(
        tubedepth_schema,
        [_artifact("channel.videos", "UC1", fetched_at=T0 - timedelta(hours=1))],
    )
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((200, _body(*VIDEO_IDS)))

    assert _work(tubedepth_schema, tmp_path, handle) == 1

    assert seen == [], "the day is spent, so not one request goes out"
    assert _job_rows(tubedepth_schema) == dict.fromkeys(VIDEO_IDS, ("failed", "budget"))


@postgres
def test_a_day_with_room_left_still_runs(tubedepth_schema: str, tmp_path: Path):
    _insert_artifacts(
        tubedepth_schema,
        [_artifact("channel.videos", "UC1", fetched_at=T0 - timedelta(hours=1))],
    )
    _queue_videos(tubedepth_schema, tmp_path)
    handle, seen = _handler((200, _body(*VIDEO_IDS)))

    assert _work(tubedepth_schema, tmp_path, handle) == 0
    assert len(seen) == 1


def test_the_daily_bound_is_beside_the_per_run_one_and_under_the_vendors_ceiling():
    """Both numbers exist, and the day's one is the wider of the two -- a per-run bound that already
    exceeded the day would be the shape #259 found (`max_requests_per_run` bounding nothing)."""
    per_run = int(transport.ROUTES[Route.DATA_API]["max_requests_per_run"])
    assert per_run > 0
    assert quota.MAX_REQUESTS_PER_DAY > per_run
    assert quota.MAX_REQUESTS_PER_DAY < 10_000  # the vendor's default daily quota, in units


def test_a_prefetching_fetcher_asks_for_nothing_it_was_not_given():
    """`prefetch` is optional on the seam and must never fetch a kind it was not handed."""
    handle, seen = _handler((200, _body(*VIDEO_IDS)))
    fetcher = _fetcher(handle)
    fetcher.prefetch([FetchSpec(kind="video.metadata", target=VIDEO_IDS[0])])
    assert _ids_of(seen[0]) == [VIDEO_IDS[0]]
    # Consumed once: the second read of the same target is a fetch of its own.
    assert fetcher.fetch(FetchSpec(kind="video.metadata", target=VIDEO_IDS[0]))["id"] == VIDEO_IDS[0]
    assert len(seen) == 1
