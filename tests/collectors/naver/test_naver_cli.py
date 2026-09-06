"""Exit codes (contracts/entrypoints.md's exit codes: 0 ok, 1 partial, 2 blocked), the secret-existence
gate (contracts/secrets.md: key names only, exit 2 when missing), and that `cosmai collect naver`
actually reaches this module -- same form as tests/collectors/youtube/test_cli.py (#8)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from collectors.naver import cli
from collectors.naver.cli import FetchSpec, run
from collectors.naver.storage.tables import naver_blog_post, naver_datalab_point, naver_fetch_log, naver_run
from collectors.naver.transport import (
    AuthBlocked,
    BudgetExhausted,
    HttpFetcher,
    RateLimited,
    RequestFailed,
    TransportError,
)

pytestmark = pytest.mark.postgres

AT = datetime(2026, 8, 24, 6, 10, tzinfo=UTC)

DATALAB_BODY = {
    "results": [
        {
            "title": "백탁",
            "keywords": ["선크림 백탁", "썬크림 백탁", "선크림 하얗게"],
            "data": [{"period": "2016-01-01", "ratio": 10.4}],
        },
        {"title": "밀림", "keywords": ["선크림 밀림"], "data": [{"period": "2016-01-01", "ratio": 5.1}]},
        {"title": "눈시림", "keywords": ["선크림 눈시림"], "data": [{"period": "2016-01-01", "ratio": 33.7}]},
        {"title": "따가움", "keywords": ["선크림 따가움"], "data": [{"period": "2016-01-01", "ratio": 1.1}]},
        {"title": "끈적임", "keywords": ["선크림 끈적임"], "data": [{"period": "2016-01-01", "ratio": 2.0}]},
    ]
}


def _blog_page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": items, "total": len(items), "display": 100, "start": 1}


class _FakeFetcher:
    """No network: one datalab response, and a single populated page per blog query (empty after)."""

    def __init__(self) -> None:
        self.calls: list[FetchSpec] = []
        self._blog_served: set[str] = set()

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.calls.append(spec)
        if spec.kind == "datalab":
            return DATALAB_BODY
        if spec.query in self._blog_served:
            return _blog_page([])
        self._blog_served.add(spec.query)
        return _blog_page(
            [
                {
                    "title": f"<b>{spec.query}</b> 후기",
                    "link": f"https://blog.naver.com/x/{spec.query.replace(' ', '_')}",
                    "description": "블로그 본문 발췌",
                    "bloggername": "누군가",
                    "postdate": "20260801",
                }
            ]
        )


class _RaisingFetcher:
    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        raise RuntimeError("blog search rate limit exceeded (429)")


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "env"
    path.write_text(
        "COSMA_SRC_NAVER_BLOG_CLIENT_ID=id\nCOSMA_SRC_NAVER_BLOG_CLIENT_SECRET=secret\n", encoding="utf-8"
    )
    return path


def test_unknown_dataset_is_blocked(needs_runtime_url: str, secret_file: Path):
    assert run("bogus", database_url=needs_runtime_url, secrets_path=secret_file) == 2


def test_missing_secret_key_is_blocked_and_names_the_key(needs_runtime_url: str, tmp_path: Path, capsys):
    empty = tmp_path / "env"
    empty.write_text("", encoding="utf-8")
    code = run("datalab", database_url=needs_runtime_url, secrets_path=empty, fetcher=_FakeFetcher())
    assert code == 2
    out = capsys.readouterr().out
    assert "COSMA_SRC_NAVER_BLOG_CLIENT_ID" in out
    assert "COSMA_SRC_NAVER_BLOG_CLIENT_SECRET" in out


def test_datalab_writes_a_point_per_group_and_month(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    code = run(
        "datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )
    assert code == 0

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(naver_datalab_point.c.group_key, naver_datalab_point.c.ratio)).all()
        run_rows = conn.execute(sa.select(naver_run.c.status, naver_run.c.dataset)).all()
        log_rows = conn.execute(sa.select(sa.func.count()).select_from(naver_fetch_log)).scalar_one()
    engine.dispose()

    assert {r.group_key for r in rows} == {"밀림", "눈시림", "백탁", "따가움", "끈적임"}
    assert run_rows == [("ok", "datalab")]
    assert log_rows == 1
    # one request covers keywords.json's one category -- the vendor's own per-request group cap.
    assert len(fetcher.calls) == 1


def test_datalab_shares_one_request_key_within_a_run_and_a_different_one_across_runs(
    needs_runtime_url: str, secret_file: Path
):
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    later = datetime(2026, 9, 1, 6, 10, tzinfo=UTC)  # a later run's endDate moves -> a new window
    run(
        "datalab",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=fetcher,
        captured_at=later,
    )

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        keys = conn.execute(sa.select(naver_datalab_point.c.request_key)).scalars().all()
    engine.dispose()

    # the second run's fresh window rescaled every group's ratio -- all 5 rows now share its key.
    assert len(set(keys)) == 1
    assert keys[0] != ""


def test_datalab_rerun_of_the_same_month_upserts_not_duplicates(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_datalab_point)).scalar_one()
    engine.dispose()
    assert n == 5  # 5 groups x 1 month, not 10


def test_datalab_is_blocked_when_the_fetcher_fails_every_category(needs_runtime_url: str, secret_file: Path):
    code = run(
        "datalab",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_RaisingFetcher(),
        captured_at=AT,
    )
    assert code == 2


def test_blog_writes_one_post_per_query_term(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    code = run(
        "blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )
    assert code == 0

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_blog_post)).scalar_one()
        sample = conn.execute(sa.select(naver_blog_post).limit(1)).mappings().one()
    engine.dispose()

    # 5 groups x 3 terms = 15 queries; the fake fetcher serves one distinct post per query term.
    assert n == 15
    assert sample["observed_at_resolution"] == "day"
    assert "<b>" not in sample["title"]


def test_blog_rerun_upserts_on_post_id_not_duplicates(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    run("blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    fetcher2 = _FakeFetcher()
    run("blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher2, captured_at=AT)

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_blog_post)).scalar_one()
    engine.dispose()
    assert n == 15


def test_blog_is_blocked_when_every_query_fails(needs_runtime_url: str, secret_file: Path):
    code = run(
        "blog",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_RaisingFetcher(),
        captured_at=AT,
    )
    assert code == 2


def test_cosmai_collect_naver_reaches_this_module():
    """Not `--help` -- that only proves the parser accepts `naver`. This proves _run_collect's
    dispatch actually imports collectors.naver.cli rather than the "not wired yet" refusal."""
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "cosmai.cli", "collect", "naver", "--dataset", "bogus"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "not wired yet" not in result.stdout
    assert "no dataset named" in result.stdout


# --- the live default, and what a status does to the run's exit code (#182) ----------------------


class _AuthBlockedFetcher:
    """The gateway refusing the credential: every later request would be refused the same way."""

    def __init__(self) -> None:
        self.calls: list[FetchSpec] = []

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.calls.append(spec)
        raise AuthBlocked("HTTP 401 (errorCode=024)", status=401, error_code="024")


class _StoppingAfter:
    """Serves `allowed` requests, then raises whatever ends a run early (429 or a spent budget)."""

    def __init__(self, error: TransportError, allowed: int = 0) -> None:
        self.error = error
        self.allowed = allowed
        self.calls = 0

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.calls += 1
        if self.calls > self.allowed:
            raise self.error
        if spec.kind == "datalab":
            return DATALAB_BODY
        if spec.params["start"] > 1:
            return _blog_page([])
        return _blog_page([_post_for(spec.query)])


class _FailingOneTerm:
    """One term's request fails; every other one is served."""

    def __init__(self, term: str) -> None:
        self.term = term

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        if spec.query == self.term:
            raise RequestFailed("HTTP 400 (errorCode=101)", status=400, error_code="101")
        if spec.params["start"] > 1:
            return _blog_page([])
        return _blog_page([_post_for(spec.query)])


def _post_for(term: str) -> dict[str, Any]:
    return {
        "title": f"{term} review",
        "link": f"https://blog.naver.com/x/{term.replace(' ', '_')}",
        "description": "an excerpt",
        "bloggername": "someone",
        "postdate": "20260801",
    }


def _run_rows(url: str) -> list[Any]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(naver_run.c.status, naver_run.c.note)).mappings().all()
    engine.dispose()
    return list(rows)


def _fetch_log_statuses(url: str) -> list[int | None]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(naver_fetch_log.c.status)).scalars().all()
    engine.dispose()
    return list(rows)


def _blog_count(url: str) -> int:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_blog_post)).scalar_one()
    engine.dispose()
    return n


def test_a_refused_credential_blocks_the_datalab_run_and_names_the_status(
    needs_runtime_url: str, secret_file: Path
):
    fetcher = _AuthBlockedFetcher()
    code = run(
        "datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )

    assert code == 2
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "blocked"
    assert "401" in row["note"] and "024" in row["note"]
    # The fetch_log status is what collector_health buckets as blocked (contracts/entrypoints.md).
    assert _fetch_log_statuses(needs_runtime_url) == [401]


def test_a_refused_credential_stops_the_blog_run_at_the_first_query(
    needs_runtime_url: str, secret_file: Path
):
    fetcher = _AuthBlockedFetcher()
    code = run(
        "blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )

    assert code == 2
    # 15 query terms, one request: a refused key is not asked fourteen more times.
    assert len(fetcher.calls) == 1
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "blocked"


@pytest.mark.parametrize(
    "error",
    [
        RateLimited("HTTP 429 (errorCode=012)", status=429, error_code="012"),
        BudgetExhausted("this run has spent its budget of 200 request(s)"),
    ],
    ids=["rate-limited", "budget-spent"],
)
def test_a_stopped_blog_run_is_partial_and_keeps_what_it_collected(
    needs_runtime_url: str, secret_file: Path, error: TransportError
):
    # Two requests served: the first term's page and its empty second page. The third request --
    # the second term's first page -- is the one that stops the run.
    fetcher = _StoppingAfter(error, allowed=2)
    code = run(
        "blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )

    assert code == 1
    assert _blog_count(needs_runtime_url) == 1
    # Stopped, not skipped: the other 14 terms are not asked after the vendor said "not now".
    assert fetcher.calls == 3
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "partial"


def test_a_rate_limited_datalab_run_is_partial_even_with_nothing_collected(
    needs_runtime_url: str, secret_file: Path
):
    """A 429 says "not now", not "never" -- exit 1 sends the operator to the next window, while the
    exit 2 a blocked run returns would send them to look for a broken credential."""
    error = RateLimited("HTTP 429 (errorCode=012)", status=429, error_code="012")
    code = run(
        "datalab",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_StoppingAfter(error),
        captured_at=AT,
    )

    assert code == 1
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "partial"
    assert _fetch_log_statuses(needs_runtime_url) == [429]


def test_one_failed_request_leaves_the_blog_run_partial(needs_runtime_url: str, secret_file: Path):
    from collectors.naver import keywords

    term = keywords.queries()[0].term
    code = run(
        "blog",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_FailingOneTerm(term),
        captured_at=AT,
    )

    assert code == 1
    assert _blog_count(needs_runtime_url) == 14  # 15 terms, the one that failed missing
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "partial"
    assert 400 in _fetch_log_statuses(needs_runtime_url)


class _RecordingLive(HttpFetcher):
    """The live transport with its socket replaced -- everything else on the path is the real code
    the CLI builds, including the retry loop and the status mapping."""

    built: list[dict[str, str]] = []

    def __init__(self, client_id: str, client_secret: str, **kwargs: Any) -> None:
        _RecordingLive.built.append({"client_id": client_id, "client_secret": client_secret})
        kwargs["transport"] = httpx.MockTransport(_serve_one_page)
        super().__init__(client_id, client_secret, **kwargs)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


def _serve_one_page(request: httpx.Request) -> httpx.Response:
    term = request.url.params["query"]
    start = int(request.url.params["start"])
    return httpx.Response(200, json=_blog_page([_post_for(term)] if start == 1 else []))


def test_the_default_fetcher_is_the_live_transport(needs_runtime_url: str, secret_file: Path, monkeypatch):
    """#182: with no `fetcher=`, a run builds the live transport out of the secret file's two keys
    and closes it. Before this, the default raised and every scheduled run ended blocked."""
    _RecordingLive.built.clear()
    monkeypatch.setattr(cli, "HttpFetcher", _RecordingLive)

    code = run("blog", database_url=needs_runtime_url, secrets_path=secret_file, captured_at=AT)

    assert code == 0
    assert _RecordingLive.built == [{"client_id": "id", "client_secret": "secret"}]
    assert _blog_count(needs_runtime_url) == 15


def test_nothing_is_left_that_refuses_to_fetch():
    assert not hasattr(cli, "_RaisingFetcher"), "the #9 placeholder is what this issue replaces"


def test_the_run_closes_the_transport_it_built(needs_runtime_url: str, secret_file: Path, monkeypatch):
    """An httpx pool left open outlives `cosmai collect` inside the cron container."""
    built: list[_RecordingLive] = []

    class _Kept(_RecordingLive):
        def __init__(self, client_id: str, client_secret: str, **kwargs: Any) -> None:
            super().__init__(client_id, client_secret, **kwargs)
            built.append(self)

    monkeypatch.setattr(cli, "HttpFetcher", _Kept)
    run("blog", database_url=needs_runtime_url, secrets_path=secret_file, captured_at=AT)

    assert [f.closed for f in built] == [True]


def test_an_injected_fetcher_is_the_callers_to_close(needs_runtime_url: str, secret_file: Path):
    # The other side of the same rule: a fetcher closed out from under the caller that made it
    # fails on that caller's *next* run, not on this one.
    class _Owned(_FailingOneTerm):
        def __init__(self) -> None:
            super().__init__(term="")
            self.closed = False

        def close(self) -> None:
            self.closed = True

    owned = _Owned()
    run("blog", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=owned, captured_at=AT)
    assert not owned.closed
