"""#100: `error_code` must separate quota exhaustion, rate limiting, other HTTP statuses, and
transport failures -- collector_health (#77) counts 403/429 as `blocked` the way commerce's
fetch_log.status already does, and youtube had no equivalent source until this classifies the raised
exception.

#183 makes the mismatch the classifier always had concrete. It was written against one transport:
`_is_quota_exceeded` parses a YouTube Data API 403 body and `_classify_error` expects a
`urllib.error.HTTPError`'s `.code`/`.read()`. There are three sources now, and only one of them has
a status at all -- yt-dlp reports a private video, a bot check and a network blip as the same
exception with the reason in prose. So the route has to be part of the classification, and the block
of route tests at the bottom is what holds it there. The `urllib` tests above are unchanged on
purpose: an error carrying no route is still read as the Data API's, which is what every caller
written before #183 meant.
"""

from __future__ import annotations

import email.message
import io
import socket
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from collectors.youtube import transport
from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.storage.tables import jobs

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 8, 24, 3, tzinfo=UTC)


def _http_error(code: int, *, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid", code, "reason", email.message.Message(), io.BytesIO(body)
    )


class _RaisingFetcher:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def fetch(self, spec: FetchSpec) -> dict:
        raise self._error


def row_code_is_blocking(error: Exception) -> bool:
    from collectors.youtube.cli import BLOCKED_CODES, _classify_error

    return _classify_error(error) in BLOCKED_CODES


def _error_code_for(tubedepth_schema: str, tmp_path, error: Exception) -> str:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
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
    exit_code = run(
        "work",
        database_url=tubedepth_schema,
        fetcher=_RaisingFetcher(error),
        payload_root=tmp_path / "p",
        captured_at=T0,
    )
    # A raised fetch is never a clean 0. Which non-zero it is depends on what was raised: Work 3's
    # "a block (403/429) is exit 2", anything else partial (1).
    assert exit_code == (2 if row_code_is_blocking(error) else 1)

    engine = sa.create_engine(tubedepth_schema)
    try:
        with engine.begin() as conn:
            row = conn.execute(sa.select(jobs.c.error_code, jobs.c.error_message)).one()
    finally:
        engine.dispose()
    assert row.error_message  # the original text must survive somewhere on the row
    return row.error_code


def test_403_with_quota_exceeded_body_is_quota(tubedepth_schema: str, tmp_path):
    body = b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'
    assert _error_code_for(tubedepth_schema, tmp_path, _http_error(403, body=body)) == "quota"


def test_403_with_a_different_reason_is_not_quota(tubedepth_schema: str, tmp_path):
    body = b'{"error": {"errors": [{"reason": "forbidden"}]}}'
    code = _error_code_for(tubedepth_schema, tmp_path, _http_error(403, body=body))
    assert code == "http_403"
    assert code != "quota"


def test_429_is_rate_limited(tubedepth_schema: str, tmp_path):
    assert _error_code_for(tubedepth_schema, tmp_path, _http_error(429)) == "rate_limited"


def test_500_is_http_500(tubedepth_schema: str, tmp_path):
    assert _error_code_for(tubedepth_schema, tmp_path, _http_error(500)) == "http_500"


def test_socket_failure_is_transport(tubedepth_schema: str, tmp_path):
    error = urllib.error.URLError(socket.gaierror("name resolution failed"))
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "transport"


def test_error_code_fits_the_column(tubedepth_schema: str, tmp_path):
    from collectors.youtube.storage.tables import jobs as jobs_table

    column_type = jobs_table.c.error_code.type
    assert isinstance(column_type, sa.String)
    assert column_type.length == 64


# --- the route decides how the failure is read (#183) -------------------------------------------


def _transport_error(kind, route, **kwargs):
    return kind("something happened", route=route, **kwargs)


def test_a_data_api_403_quota_body_is_still_quota_when_the_route_is_named(tubedepth_schema, tmp_path):
    body = b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'
    error = _transport_error(transport.Blocked, transport.Route.DATA_API, code=403, body=body)
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "quota"


def test_a_timedtext_403_is_never_quota_even_with_a_quota_shaped_body(tubedepth_schema, tmp_path):
    """Captions are not metered. Reading a timedtext 403 for `quotaExceeded` could only ever find
    somebody else's body, and calling it quota would send an operator to the Data API console over a
    refusal that happened somewhere else entirely."""
    body = b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'
    error = _transport_error(transport.Blocked, transport.Route.TIMEDTEXT, code=403, body=body)
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "http_403"


def test_a_ytdlp_bot_check_is_rate_limited_with_no_status_to_read(tubedepth_schema, tmp_path):
    """The one yt-dlp failure that is evidence about the address rather than about the video. It has
    no HTTP status, so the pre-#183 classifier would have bucketed it as `transport` -- indistinguishable
    from a DNS failure, and in the `failed` column instead of `blocked`."""
    error = transport.ytdlp_error("Sign in to confirm you\u2019re not a bot", target="dQw4w9WgXcQ")
    assert transport.ytdlp_error("Sign in to confirm you're not a bot", target="x").route == "ytdlp"
    error = transport.ytdlp_error("ERROR: Sign in to confirm you're not a bot", target="dQw4w9WgXcQ")
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "rate_limited"


def test_a_private_video_is_unavailable_and_not_blocked(tubedepth_schema, tmp_path):
    """`unavailable` lands in collector_health's `failed`, not its `blocked`: one channel's access
    policy is not the collector being refused, and counting it as a block would make a healthy run
    look like a quota problem."""
    error = transport.ytdlp_error("ERROR: [youtube] xxx: Private video", target="xxx")
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "unavailable"


def test_an_unrecognised_ytdlp_message_is_transport(tubedepth_schema, tmp_path):
    error = transport.ytdlp_error("ERROR: something nobody has read yet", target="xxx")
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "transport"


def test_the_run_stopping_itself_is_not_reported_as_the_source_blocking_us(tubedepth_schema, tmp_path):
    """A spent request budget is our own cap. Calling it `rate_limited` would put it into
    collector_health's `blocked` count and make the collector look throttled by YouTube."""
    error = transport.BudgetExhausted("spent", route=transport.Route.YTDLP)
    assert _error_code_for(tubedepth_schema, tmp_path, error) == "budget"


def test_every_code_this_classifier_produces_fits_the_column():
    """`error_code` is varchar(64); the vocabulary has to stay inside it as it grows."""
    from collectors.youtube.cli import _classify_error

    produced = {
        _classify_error(transport.BudgetExhausted("x", route=transport.Route.DATA_API)),
        _classify_error(transport.ytdlp_error("ERROR: Private video", target="x")),
        _classify_error(transport.RateLimited("x", route=transport.Route.DATA_API, code=429)),
        _classify_error(transport.RequestFailed("x", route=transport.Route.TIMEDTEXT, code=503)),
        _classify_error(_http_error(500)),
    }
    assert produced == {"budget", "unavailable", "rate_limited", "http_503", "http_500"}
    assert all(len(code) <= 64 for code in produced)


# --- Work 3's exit code (#183 fix round) ---------------------------------------------------------


def _work_once(tubedepth_schema, tmp_path, error: Exception, *, videos: int = 1) -> int:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("".join(f"video {'abcdefghijk'[:10]}{index}\n" for index in range(videos)))
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
    return run(
        "work",
        database_url=tubedepth_schema,
        fetcher=_RaisingFetcher(error),
        payload_root=tmp_path / "p",
        captured_at=T0,
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(403, body=b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'), 2),
        (_http_error(429), 2),
        (_http_error(403, body=b'{"error": {"errors": [{"reason": "forbidden"}]}}'), 2),
        (_http_error(500), 1),
        (transport.ytdlp_error("ERROR: [youtube] x: Private video", target="x"), 1),
        (transport.BudgetExhausted("spent", route=transport.Route.YTDLP), 1),
    ],
)
def test_a_block_is_exit_two_and_everything_else_is_partial(
    tubedepth_schema, tmp_path, error: Exception, expected: int
):
    """Work 3: "a block (403/429) is exit 2". The classification half shipped and the exit-code half
    did not -- `_run_work` returned 1 for everything, so a quota-exhausted run and a run where one
    video was private looked identical to cron and to an operator."""
    assert _work_once(tubedepth_schema, tmp_path, error) == expected


def test_the_blocking_set_is_the_one_the_health_view_counts():
    """The exit code and `db/views/collector_health.sql` must never call the same run two different
    things -- the mismatch contracts/entrypoints.md already records for NAVER's 401 (#182 M2)."""
    from collectors.youtube.cli import BLOCKED_CODES

    view = (Path(__file__).resolve().parents[3] / "db" / "views" / "collector_health.sql").read_text(
        encoding="utf-8"
    )
    for code in BLOCKED_CODES:
        assert f"'{code}'" in view, f"{code} is an exit-2 code the view does not count as blocked"


def test_a_block_stops_the_batch_and_returns_the_untried_jobs_to_the_queue(tubedepth_schema, tmp_path):
    """Every later request would be refused the same way and would only deepen it. The claimed jobs
    that were never attempted go back to QUEUED rather than being stranded in RUNNING -- nothing
    here reclaims a job whose worker stopped, since the lease machinery is out of scope."""
    quota = _http_error(403, body=b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
    assert _work_once(tubedepth_schema, tmp_path, quota, videos=4) == 2

    engine = sa.create_engine(tubedepth_schema)
    try:
        with engine.begin() as conn:
            states = conn.execute(sa.select(jobs.c.state, sa.func.count()).group_by(jobs.c.state)).all()
    finally:
        engine.dispose()
    by_state = {state: count for state, count in states}
    assert by_state.get("failed") == 1, "only the job that hit the block is failed"
    assert by_state.get("queued") == 3, "the rest are queued again, not left running"
    assert "running" not in by_state
