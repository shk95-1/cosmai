"""`cosmai collect naver` -- wired for #9, live since #182. With no `fetcher=` the run builds
`transport.HttpFetcher` out of the two secret keys and really calls the NAVER API Hub; tests plug a
fixture-backed fake in, the same shape `collectors.commerce.cli`'s `Fetcher` uses.

What a status does to the run (#182, contracts/entrypoints.md's exit codes):

  401 / 403       the credential is refused -- the run stops, blocked (2), the note naming the
                  status and the vendor's errorCode (never its errorMessage, which echoes the request)
  429             the vendor says "not now" -- the run stops: partial (1) when it wrote rows,
                  blocked (2) when it wrote none (partial means we yielded, blocked means we were refused)
  budget spent    scope.MAX_REQUESTS_PER_RUN reached -- the same stop as a 429
  other 4xx       that one request failed; the run goes on and ends partial if anything failed
  5xx / timeout   retried scope.RETRY_MAX_ATTEMPTS times with a doubling backoff, then a failed request

A blog `start` past scope.BLOG_START_MAX is never requested (#110): the ceiling is the vendor's own
and independent of `total`, so the walk stops there rather than spending budget on an error.

Two datasets, matching contracts/entrypoints.md's `naver datasets: datalab | blog`. `datalab` asks
one request per category (keywords.json's groups all fit in one call -- scope.json's
max_groups_per_request is the vendor's own cap, and keywords.json's one category has exactly that
many groups). `blog` pages through every keyword-group term, one query at a time (the blog search
endpoint takes a single `query` string, unlike DataLab's grouped keywords). Exit codes follow
contracts/entrypoints.md's exit codes: 0 ok, 1 partial, 2 blocked.
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
from collectors.naver.transport import (
    AuthBlocked,
    BudgetExhausted,
    HttpFetcher,
    RateLimited,
    TransportError,
)
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

    found = secrets.load(secrets_path)
    missing = [k for k in SECRET_KEYS if not found.get(k)]
    if missing:
        print(f"missing secret key(s) in the secret file: {', '.join(missing)}")
        return 2

    now = captured_at or datetime.now(UTC)
    engine = storage_db.create_engine(database_url or storage_db.runtime_url())
    log = storage_db.RunLog(engine)
    run_id = log.start(wanted.value, now)
    journal = storage_db.FetchJournal(engine, run_id, wanted.value)
    # An injected fetcher stays its caller's to close; the one built here is this run's, and an
    # httpx pool left open outlives `cosmai collect` inside the cron container.
    built: HttpFetcher | None = None

    try:
        # Inside the try, after log.start: a constructor that raises out here would otherwise leave
        # this run at `running` forever, with nothing to say why.
        if fetcher is None:
            built = HttpFetcher(found[SECRET_KEYS[0]], found[SECRET_KEYS[1]])
            active_fetcher: Fetcher = built
        else:
            active_fetcher = fetcher
        if wanted is Dataset.DATALAB:
            outcome = _run_datalab(engine, active_fetcher, journal, now=now)
        else:
            outcome = _run_blog(engine, active_fetcher, journal, now=now)
    except BaseException as exc:
        log.finish(run_id, status="failed", note=f"{type(exc).__name__}: {exc}")
        engine.dispose()
        raise
    finally:
        if built is not None:
            built.close()

    log.finish(run_id, status=outcome.status, note=outcome.note)
    engine.dispose()
    return outcome.exit_code


def _run_datalab(engine, fetcher: Fetcher, journal, *, now: datetime) -> _Outcome:
    categories = keywords.load()
    if not categories:
        return _Outcome("blocked", 2, "keywords.json names no category")

    total_points = 0
    blocked: list[str] = []
    stopped: TransportError | None = None
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
        except AuthBlocked as error:
            # The credential is refused, not this category: every later request would be refused the
            # same way, so the run ends here rather than spending the rest of the budget on 401s.
            journal.record(query=category, status=error.status, attempt=1, error=str(error))
            return _Outcome("blocked", 2, f"datalab blocked: {error}")
        except (RateLimited, BudgetExhausted) as error:
            journal.record(query=category, status=error.status, attempt=1, error=str(error))
            stopped = error
            break
        except Exception as error:  # noqa: BLE001 - one category's failure must not stop the run
            journal.record(query=category, status=getattr(error, "status", None), attempt=1, error=str(error))
            blocked.append(category)
            continue
        journal.record(query=category, status=200, attempt=1)
        # request_key from the params actually sent -- not the response, which never echoes them
        # back reliably enough to reconstruct the boundary (#44).
        request_key = parsing.datalab_request_key(spec.params)
        points = parsing.parse_datalab_response(
            body, category=category, captured_at=now, request_key=request_key
        )
        if not points:
            blocked.append(category)
            continue
        with engine.begin() as connection:
            storage_db.write_datalab_points(connection, points)
        total_points += len(points)

    if stopped is not None:
        # partial means the run yielded something, blocked means it was refused -- a stop with no row
        # written is a refusal, and pipeline_health reads partial as "it ran" (review B1).
        print(f"datalab: {total_points} point(s) before the run stopped")
        if total_points == 0:
            return _Outcome("blocked", 2, f"datalab stopped: {stopped}")
        return _Outcome("partial", 1, f"datalab stopped: {stopped}")
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
    stopped: TransportError | None = None
    for q in queries:
        posted, failed, stopped = _walk_blog_query(engine, fetcher, journal, q, now=now)
        total_posts += posted
        if failed:
            failed_terms.append(q.term)
        if stopped is not None:
            break

    if isinstance(stopped, AuthBlocked):
        return _Outcome("blocked", 2, f"blog blocked: {stopped}")
    if stopped is not None:
        print(f"blog: {total_posts} post(s) before the run stopped")
        # The same split as datalab above: nothing written is a refusal, not a partial yield (B1).
        if total_posts == 0:
            return _Outcome("blocked", 2, f"blog stopped: {stopped}")
        return _Outcome("partial", 1, f"blog stopped: {stopped}")
    if failed_terms and total_posts == 0:
        return _Outcome("blocked", 2, f"every query failed: {', '.join(failed_terms)}")
    print(f"blog: {total_posts} post(s) across {len(queries)} quer(y/ies)")
    if failed_terms:
        return _Outcome("partial", 1, f"{len(failed_terms)} quer(y/ies) failed: {', '.join(failed_terms)}")
    return _Outcome("ok", 0, None)


def _walk_blog_query(
    engine, fetcher: Fetcher, journal, q: keywords.Query, *, now: datetime
) -> tuple[int, bool, TransportError | None]:
    """Pages one term until an empty page, the page cap, or the vendor's `start` ceiling -- mirrors
    the original collector.naver.blog's termination rule (never on `total`, which the vendor's own
    docs do not promise stays still across calls).

    Returns what this term posted, whether it failed, and -- when there is one -- the error that ends
    the whole run. The error is returned rather than raised so the pages already written stay in the
    count `_run_blog` prints."""
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
        except (AuthBlocked, RateLimited, BudgetExhausted) as error:
            # These three end the run, not the term: a refused credential, a rate limit and a spent
            # budget all say the same thing about every request still to come.
            journal.record(query=q.term, status=error.status, attempt=attempt, error=str(error))
            return posted, False, error
        except Exception as error:  # noqa: BLE001 - one query's failure must not stop the run
            journal.record(
                query=q.term, status=getattr(error, "status", None), attempt=attempt, error=str(error)
            )
            return posted, True, None
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
    return posted, False, None


__all__ = ["run", "Fetcher", "FetchSpec"]
