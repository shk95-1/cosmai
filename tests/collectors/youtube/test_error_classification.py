"""#100: `error_code` must separate quota exhaustion, rate limiting, other HTTP statuses, and
transport failures -- collector_health (#77) counts 403/429 as `blocked` the way commerce's
fetch_log.status already does, and youtube had no equivalent source until this classifies the raised
exception."""

from __future__ import annotations

import email.message
import io
import socket
import urllib.error
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

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


def _error_code_for(tubedepth_schema: str, tmp_path, error: Exception) -> str:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    assert run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=T0) == 0
    exit_code = run(
        "work",
        database_url=tubedepth_schema,
        fetcher=_RaisingFetcher(error),
        payload_root=tmp_path / "p",
        captured_at=T0,
    )
    assert exit_code == 1  # a raised fetch is a partial run, not a clean 0

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
