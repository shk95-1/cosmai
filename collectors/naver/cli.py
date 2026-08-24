"""`cosmai collect naver` -- wired for #9. No live transport: `fetcher` is the seam a real cutover
(#10, "라이브 호출 없음" here) plugs a `Fetcher` backed by the Naver API Hub into; tests plug a
fixture-backed fake in, the same shape `collectors.commerce.cli`'s `Fetcher` uses.

Two datasets, matching contracts/entrypoints.md's `naver datasets: datalab | blog`. `datalab` asks
one request per category (keywords.json's groups all fit in one call -- scope.json's
max_groups_per_request is the vendor's own cap, and keywords.json's one category has exactly that
many groups). `blog` pages through every keyword-group term, one query at a time (the blog search
endpoint takes a single `query` string, unlike DataLab's grouped keywords). Exit codes follow
contracts/entrypoints.md 종료 코드: 0 ok, 1 partial, 2 blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from collectors.naver import keywords, parsing
from collectors.naver.models import Dataset
from collectors.naver.scope import (
    BLOG_DISPLAY,
    BLOG_PAGES_MAX,
    BLOG_SORT,
    BLOG_START_MAX,
    DATALAB_MAX_GROUPS_PER_REQUEST,
    DATALAB_TIME_UNIT,
    DATALAB_WINDOW_START,
)
from collectors.naver.storage import db as storage_db
from db import secrets

#: secrets.md names one credential pair for the whole collector -- both datasets go through the
#: same Naver API Hub gateway key, registered once (unlike the original addon's per-source
#: outbound_profile, which this repo does not carry forward -- issue #9 judgment (a)).
SECRET_KEYS = ("COSMA_SRC_NAVER_BLOG_CLIENT_ID", "COSMA_SRC_NAVER_BLOG_CLIENT_SECRET")

#: keywords.json's one category today. A second category is a config change, not a code change --
#: `_run_datalab`/`_run_blog` both walk every category `keywords.load()` names.


class Fetcher(Protocol):
    def fetch(self, spec: FetchSpec) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class FetchSpec:
    kind: str  # "datalab" | "blog"
    query: str  # what fetch_log.query records: a group name (datalab) or a search term (blog)
    params: dict[str, Any]


class _RaisingFetcher:
    """The default fetcher: fails loudly rather than opening a real socket. A live cutover (#10)
    replaces this."""

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:  # pragma: no cover - only if actually called
        raise NotImplementedError(
            "collectors.naver has no live transport yet; see issue #10 (cutover). "
            "Tests inject a fixture-backed fetcher instead of calling the CLI's default."
        )


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str  # ok | partial | blocked
    exit_code: int
    note: str | None


def run(
    dataset: str,
    board: str | None = None,
    since: str | None = None,
    *,
    database_url: str | None = None,
    fetcher: Fetcher | None = None,
    secrets_path: str | Path | None = None,
    captured_at: datetime | None = None,
) -> int:
    """Run one dataset for one pass. `board`/`since` are accepted for the entrypoint's shape
    (contracts/entrypoints.md); neither means anything to either naver dataset today."""
    del board, since
    try:
        wanted = Dataset(dataset)
    except ValueError:
        known = ", ".join(d.value for d in Dataset)
        print(f"no dataset named {dataset!r}; known: {known}")
        return 2

    missing = [k for k in SECRET_KEYS if not secrets.load(secrets_path).get(k)]
    if missing:
        print(f"missing secret key(s) in the secret file: {', '.join(missing)}")
        return 2

    now = captured_at or datetime.now(UTC)
    engine = storage_db.create_engine(database_url or storage_db.runtime_url())
    log = storage_db.RunLog(engine)
    run_id = log.start(wanted.value, now)
    journal = storage_db.FetchJournal(engine, run_id, wanted.value)
    active_fetcher = fetcher or _RaisingFetcher()

    try:
        if wanted is Dataset.DATALAB:
            outcome = _run_datalab(engine, active_fetcher, journal, now=now)
        else:
            outcome = _run_blog(engine, active_fetcher, journal, now=now)
    except BaseException as exc:
        log.finish(run_id, status="failed", note=f"{type(exc).__name__}: {exc}")
        engine.dispose()
        raise

    log.finish(run_id, status=outcome.status, note=outcome.note)
    engine.dispose()
    return outcome.exit_code


def _run_datalab(engine, fetcher: Fetcher, journal, *, now: datetime) -> _Outcome:
    categories = keywords.load()
    if not categories:
        return _Outcome("blocked", 2, "keywords.json names no category")

    total_points = 0
    blocked: list[str] = []
    for category, groups in categories.items():
        if len(groups) > DATALAB_MAX_GROUPS_PER_REQUEST:
            # A config that outgrows the vendor's own cap is a keywords.json bug, not a runtime
            # fault -- fail this category loudly rather than silently dropping groups past the cap.
            blocked.append(category)
            continue
        spec = FetchSpec(
            kind="datalab",
            query=category,
            params={
                "keywordGroups": [{"groupName": g, "keywords": list(t)} for g, t in groups.items()],
                "startDate": DATALAB_WINDOW_START,
                "endDate": now.date().isoformat(),
                "timeUnit": DATALAB_TIME_UNIT,
            },
        )
        try:
            body = fetcher.fetch(spec)
        except Exception as error:  # noqa: BLE001 - one category's failure must not stop the run
            journal.record(query=category, status=None, attempt=1, error=str(error))
            blocked.append(category)
            continue
        journal.record(query=category, status=200, attempt=1)
        points = parsing.parse_datalab_response(body, category=category, captured_at=now)
        if not points:
            blocked.append(category)
            continue
        with engine.begin() as connection:
            storage_db.write_datalab_points(connection, points)
        total_points += len(points)

    if blocked and total_points == 0:
        return _Outcome("blocked", 2, f"no points from: {', '.join(blocked)}")
    print(f"datalab: {total_points} point(s) across {len(categories)} categor(y/ies)")
    if blocked:
        return _Outcome("partial", 1, f"no points from: {', '.join(blocked)}")
    return _Outcome("ok", 0, None)


def _run_blog(engine, fetcher: Fetcher, journal, *, now: datetime) -> _Outcome:
    queries = keywords.queries()
    if not queries:
        return _Outcome("blocked", 2, "keywords.json names no query")

    total_posts = 0
    failed_terms: list[str] = []
    for q in queries:
        posted, failed = _walk_blog_query(engine, fetcher, journal, q, now=now)
        total_posts += posted
        if failed:
            failed_terms.append(q.term)

    if failed_terms and total_posts == 0:
        return _Outcome("blocked", 2, f"every query failed: {', '.join(failed_terms)}")
    print(f"blog: {total_posts} post(s) across {len(queries)} quer(y/ies)")
    if failed_terms:
        return _Outcome("partial", 1, f"{len(failed_terms)} quer(y/ies) failed: {', '.join(failed_terms)}")
    return _Outcome("ok", 0, None)


def _walk_blog_query(
    engine, fetcher: Fetcher, journal, q: keywords.Query, *, now: datetime
) -> tuple[int, bool]:
    """Pages one term until an empty page, the page cap, or the vendor's `start` ceiling -- mirrors
    the original collector.naver.blog's termination rule (never on `total`, which the vendor's own
    docs do not promise stays still across calls)."""
    start = 1
    posted = 0
    for attempt in range(1, BLOG_PAGES_MAX + 1):
        if start > BLOG_START_MAX:
            break
        params: dict[str, Any] = {"query": q.term, "display": BLOG_DISPLAY, "start": start}
        if BLOG_SORT:
            params["sort"] = BLOG_SORT
        spec = FetchSpec(kind="blog", query=q.term, params=params)
        try:
            body = fetcher.fetch(spec)
        except Exception as error:  # noqa: BLE001 - one query's failure must not stop the run
            journal.record(query=q.term, status=None, attempt=attempt, error=str(error))
            return posted, True
        journal.record(query=q.term, status=200, attempt=attempt)
        posts = parsing.parse_blog_response(
            body, category=q.category, group_key=q.group_key, query=q.term, captured_at=now
        )
        if posts:
            with engine.begin() as connection:
                storage_db.write_blog_posts(connection, posts)
            posted += len(posts)
        if parsing.blog_page_is_empty(body):
            break
        start += BLOG_DISPLAY
    return posted, False


__all__ = ["run", "Fetcher", "FetchSpec"]
