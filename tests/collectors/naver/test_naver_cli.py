"""Exit codes (contracts/entrypoints.md's exit codes: 0 ok, 1 partial, 2 blocked), the secret-existence
gate (contracts/secrets.md: key names only, exit 2 when missing), and that `cosmai collect naver`
actually reaches this module -- same form as tests/collectors/youtube/test_cli.py (#8)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from collectors.naver import cli, keywords, scope
from collectors.naver.cli import FetchSpec, run
from collectors.naver.storage.tables import (
    naver_blog_post,
    naver_datalab_anchor,
    naver_datalab_point,
    naver_fetch_log,
    naver_run,
)
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
VIEW_SQL = Path(__file__).resolve().parents[3] / "db" / "views" / "naver_datalab_rescaled.sql"


def _datalab_body(spec: FetchSpec, empty: Collection[str] = ()) -> dict[str, Any]:
    """One series per group the request asked for -- the vendor echoes the `groupName` back as
    `title`, which is what the parser keys `group_key` off. Built from the request rather than
    fixed, because since #90 what a request holds is the thing under test (the anchor plus at most
    four of a category's groups).

    A group named in `empty` comes back the way a term nobody searches really does (#250): the
    series is present and its `data` array is empty. Until this parameter existed every group always
    carried a point, so no test had ever seen the case that hid the defect."""
    return {
        "results": [
            {
                "title": group["groupName"],
                "keywords": list(group["keywords"]),
                "data": (
                    []
                    if group["groupName"] in empty
                    else [{"period": "2016-01-01", "ratio": 10.0 + 10.0 * index}]
                ),
            }
            for index, group in enumerate(spec.params["keywordGroups"])
        ]
    }


def _blog_page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"items": items, "total": len(items), "display": 100, "start": 1}


class _FakeFetcher:
    """No network: one datalab response, and a single populated page per blog query (empty after).

    `empty_groups` names the datalab groups whose series comes back with an empty `data` array."""

    def __init__(self, empty_groups: Collection[str] = ()) -> None:
        self.calls: list[FetchSpec] = []
        self._empty_groups = frozenset(empty_groups)
        self._blog_served: set[str] = set()

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        self.calls.append(spec)
        if spec.kind == "datalab":
            return _datalab_body(spec, self._empty_groups)
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

    # The anchor is stored like any other group (#90), so it stands beside keywords.json's own.
    declared = {group for groups in keywords.load().values() for group in groups}
    assert {r.group_key for r in rows} == declared | {scope.DATALAB_ANCHOR}
    assert run_rows == [("ok", "datalab")]
    # Two requests for the one category: five groups do not fit beside the anchor (#90).
    assert log_rows == 2
    assert len(fetcher.calls) == 2


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

    # The second run's fresh window rescaled every group's ratio. Its two requests are two
    # boundaries, one per batch of groups (#90), and no key of the first run survives.
    assert len(set(keys)) == 2
    assert all(k != "" for k in keys)


def test_datalab_rerun_of_the_same_month_upserts_not_duplicates(needs_runtime_url: str, secret_file: Path):
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        n = conn.execute(sa.select(sa.func.count()).select_from(naver_datalab_point)).scalar_one()
    engine.dispose()
    assert n == 6  # 5 groups + the anchor, x 1 month, not 12


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
            return _datalab_body(spec)
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


def test_a_rate_limited_datalab_run_with_nothing_collected_is_blocked(
    needs_runtime_url: str, secret_file: Path
):
    """Review B1: partial means the run yielded something. A 429 on the first request yields nothing,
    and pipeline_health reads partial as "it ran" -- a month of `fresh` over an empty table. The pair
    with `..._keeps_what_it_collected` above is the whole split: rows written -> 1, none -> 2."""
    error = RateLimited("HTTP 429 (errorCode=012)", status=429, error_code="012")
    code = run(
        "datalab",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_StoppingAfter(error),
        captured_at=AT,
    )

    assert code == 2
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "blocked"
    assert "429" in row["note"]
    assert _fetch_log_statuses(needs_runtime_url) == [429]


def test_a_stopped_blog_run_that_wrote_nothing_is_blocked(needs_runtime_url: str, secret_file: Path):
    """The blog half of B1's split: the same stop, no rows, exit 2."""
    error = BudgetExhausted("this run has spent its budget of 200 request(s)")
    code = run(
        "blog",
        database_url=needs_runtime_url,
        secrets_path=secret_file,
        fetcher=_StoppingAfter(error),
        captured_at=AT,
    )

    assert code == 2
    assert _blog_count(needs_runtime_url) == 0
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "blocked"


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


def test_every_datalab_request_carries_the_anchor_group(needs_runtime_url: str, secret_file: Path):
    """#90: without the anchor in every request there is nothing two requests share, and their
    ratios cannot be put on one scale afterwards."""
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    assert fetcher.calls, "no datalab request was sent"
    for spec in fetcher.calls:
        groups = spec.params["keywordGroups"]
        anchors = [g for g in groups if g["groupName"] == scope.DATALAB_ANCHOR]
        expected = {"groupName": scope.DATALAB_ANCHOR, "keywords": list(scope.DATALAB_ANCHOR_TERMS)}
        assert anchors == [expected], groups


def test_a_datalab_request_never_passes_the_vendors_group_cap(needs_runtime_url: str, secret_file: Path):
    # The anchor takes one of the vendor's five slots, so at most four category groups ride beside it.
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    for spec in fetcher.calls:
        groups = spec.params["keywordGroups"]
        assert len(groups) <= scope.DATALAB_MAX_GROUPS_PER_REQUEST, groups
        beside = [g for g in groups if g["groupName"] != scope.DATALAB_ANCHOR]
        assert len(beside) <= scope.DATALAB_CATEGORY_GROUPS_PER_REQUEST, groups


def test_a_categorys_groups_are_all_asked_for_across_the_requests(needs_runtime_url: str, secret_file: Path):
    # Batching must not drop a group: the union over the requests is the category's whole group set.
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    asked = [
        (spec.query.split()[0], g["groupName"], tuple(g["keywords"]))
        for spec in fetcher.calls
        for g in spec.params["keywordGroups"]
        if g["groupName"] != scope.DATALAB_ANCHOR
    ]
    wanted = [
        (category, group, terms)
        for category, groups in keywords.load().items()
        for group, terms in groups.items()
    ]
    assert sorted(asked) == sorted(wanted)
    assert len(asked) == len({(c, g) for c, g, _ in asked}), "a group was asked for twice"


def test_the_request_count_is_one_per_batch_of_four_groups():
    """The arithmetic the daily call ceiling is read against (#90): a category of N groups costs
    ceil(N / 4) requests, where it cost ceil(N / 5) before the anchor."""
    per_request = scope.DATALAB_CATEGORY_GROUPS_PER_REQUEST
    for count, expected in ((1, 1), (4, 1), (5, 2), (8, 2), (9, 3)):
        groups = {f"g{i}": (f"t{i}",) for i in range(count)}
        batches = cli.datalab_request_batches(groups)
        assert len(batches) == expected, (count, batches)
        assert all(len(b) <= per_request for b in batches), batches
        assert {g for b in batches for g in b} == set(groups)


def test_the_batches_grow_so_the_last_one_carries_the_most_groups():
    """The anchor row's key is (category, group_key, month), so a category that takes more than one
    request keeps only the last request's anchor -- the earlier batches' rows rescale to NULL
    (contracts/formats.md). Sending the remainder first is what keeps that loss at its minimum."""
    batches = cli.datalab_request_batches({f"g{i}": (f"t{i}",) for i in range(5)})
    assert [len(b) for b in batches] == [1, 4]


def test_the_anchor_rows_carry_the_request_key_of_the_batch_they_came_with(
    needs_runtime_url: str, secret_file: Path
):
    # naver_datalab_point still gets an anchor row per #90 -- its PK (category, group_key, month)
    # means only the last request's write survives there, unlike needs.naver_datalab_anchor (#248),
    # which the rescale view actually joins on.
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(naver_datalab_point.c.group_key, naver_datalab_point.c.request_key)
        ).all()
    engine.dispose()

    by_group = {str(group_key): str(request_key) for group_key, request_key in rows}
    anchor_key = by_group[scope.DATALAB_ANCHOR]
    last = fetcher.calls[-1]
    sent_last = {g["groupName"] for g in last.params["keywordGroups"]} - {scope.DATALAB_ANCHOR}
    assert sent_last, last.params["keywordGroups"]
    assert {by_group[g] for g in sent_last} == {anchor_key}


def test_every_batch_leaves_its_own_anchor_row(needs_runtime_url: str, secret_file: Path):
    """#248: needs.naver_datalab_anchor is keyed by (request_key, month), so unlike
    naver_datalab_point's PK it does not let the second batch's write erase the first's."""
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)
    assert len(fetcher.calls) == 2  # keywords.json's one 5-group category needs two requests

    engine = sa.create_engine(needs_runtime_url)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(naver_datalab_anchor.c.request_key, naver_datalab_anchor.c.ratio)).all()
    engine.dispose()

    sent_keys = {
        cli.parsing.datalab_request_key(spec.params) for spec in fetcher.calls if spec.kind == "datalab"
    }
    assert {str(r.request_key) for r in rows} == sent_keys
    assert all(r.ratio is not None for r in rows)


def test_a_five_group_category_collected_in_two_requests_rescales_all_five(
    needs_runtime_url: str, secret_file: Path, database_url_for_tests: str, _schema_name: str
):
    """The defect this issue closes (#248): naver_datalab_point's PK (category, group_key, month)
    let the second batch's anchor overwrite the first's, so the smallest batch's groups -- the
    remainder, whichever `keywords.json`'s groups the remainder-first cut sends first -- rescaled
    to NULL though their own request carried a real anchor point. With the anchor kept per request,
    every group rescales, the smallest batch's included."""
    fetcher = _FakeFetcher()
    code = run(
        "datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )
    assert code == 0
    assert len(fetcher.calls) == 2  # the one 5-group category needs two requests beside the anchor

    engine = sa.create_engine(database_url_for_tests)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(VIEW_SQL.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    engine.dispose()

    reader = sa.create_engine(needs_runtime_url)
    with reader.begin() as conn:
        rows = conn.execute(sa.text("SELECT group_key, ratio_rescaled FROM naver_datalab_rescaled")).all()
    reader.dispose()

    by_group = {str(r.group_key): r.ratio_rescaled for r in rows}
    declared = {group for groups in keywords.load().values() for group in groups}
    for group in declared | {scope.DATALAB_ANCHOR}:
        assert by_group[group] is not None, group

    # The specific case #248 closes: whichever group the remainder-first cut sent in the smaller,
    # earlier batch -- the one whose anchor an overwriting join left NULL -- also rescales.
    batches = cli.datalab_request_batches(keywords.groups(next(iter(keywords.load()))))
    smallest_batch_group = next(iter(min(batches, key=len)))
    assert by_group[smallest_batch_group] is not None, smallest_batch_group


# --- the anchor is a label over a real term, and an empty series is visible (#250) ----------------


def _datalab_group_keys(url: str) -> set[str]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        keys = conn.execute(sa.select(naver_datalab_point.c.group_key)).scalars().all()
    engine.dispose()
    return {str(k) for k in keys}


def _declared_groups() -> set[str]:
    return {group for groups in keywords.load().values() for group in groups}


def test_the_anchor_group_is_a_label_over_a_real_search_term(needs_runtime_url: str, secret_file: Path):
    """#250: `groupName` is only a label the vendor echoes back as `results[].title`, and `keywords`
    is what is actually searched. The label went out as its own keyword, that token has no search
    volume, DataLab answered with an empty `data` array, and every rescaled row was NULL."""
    fetcher = _FakeFetcher()
    run("datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT)

    assert fetcher.calls, "no datalab request was sent"
    for spec in fetcher.calls:
        anchor = [g for g in spec.params["keywordGroups"] if g["groupName"] == scope.DATALAB_ANCHOR]
        assert anchor and anchor[0]["keywords"] == list(scope.DATALAB_ANCHOR_TERMS), spec.params
        assert scope.DATALAB_ANCHOR not in anchor[0]["keywords"], "the label is not a search term"


def test_a_group_that_comes_back_with_an_empty_series_stores_no_point(
    needs_runtime_url: str, secret_file: Path
):
    """The case that hid #250: a requested group whose response carries an empty `data` array. It
    stores nothing, and the rest of the request is written as usual -- which is why the run stayed
    ok for a fortnight with no anchor row in the table at all."""
    silent = sorted(_declared_groups())[0]
    fetcher = _FakeFetcher(empty_groups={silent})
    code = run(
        "datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )

    assert code == 0
    assert silent in {g["groupName"] for spec in fetcher.calls for g in spec.params["keywordGroups"]}
    assert _datalab_group_keys(needs_runtime_url) == (_declared_groups() - {silent} | {scope.DATALAB_ANCHOR})


def test_a_datalab_request_that_got_no_anchor_back_leaves_the_run_partial(
    needs_runtime_url: str, secret_file: Path
):
    """#250 item 3: with no anchor point for a request, every one of that request's rows rescales to
    NULL (`db/views/naver_datalab_rescaled.sql`). An ok run is the state this issue exists against,
    so the run reports partial (1) with a note naming the anchor -- it did yield rows, so it is not
    blocked (contracts/entrypoints.md)."""
    fetcher = _FakeFetcher(empty_groups={scope.DATALAB_ANCHOR})
    code = run(
        "datalab", database_url=needs_runtime_url, secrets_path=secret_file, fetcher=fetcher, captured_at=AT
    )

    assert code == 1
    (row,) = _run_rows(needs_runtime_url)
    assert row["status"] == "partial"
    assert scope.DATALAB_ANCHOR in row["note"], row["note"]
    # The category's own points are kept: a raw ratio stays comparable inside its own request_key.
    assert _datalab_group_keys(needs_runtime_url) == _declared_groups()
