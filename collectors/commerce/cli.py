"""`cosmai collect commerce` -- wired for #7, given a live transport for #10.

Until #10 the default fetcher was a `_RaisingFetcher`: every scheduled run ended in
NotImplementedError, and stack/docker-compose.yml said so in a comment. It is now
`collectors/commerce/transport`, built per source. `fetcher` stays the injection seam -- tests hand
in a fixture-backed fake, and anything passed there is used instead of opening a socket.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce import sources as _sources  # noqa: F401 -- import registers every source
from collectors.commerce.engine import Fetcher, RunReport, collect, exit_code_for
from collectors.commerce.models import Dataset, hour_bucket
from collectors.commerce.registry import SOURCES
from collectors.commerce.storage import db as storage_db
from collectors.commerce.storage.db import PostgresJournal, RunLog, create_engine
from collectors.commerce.storage.locks import PostgresSourceLock
from collectors.commerce.transport import LiveFetchers

SCOPE_PATH = Path(__file__).resolve().parent / "scope.json"


def _scope() -> dict:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def review_low_boards() -> list[str]:
    """Every board `--dataset review_low --board <name>` may name, as scope.json declares them."""
    boards: set[str] = set()
    for source_scope in _scope().values():
        low = source_scope.get("review_low") if isinstance(source_scope, dict) else None
        if low:
            boards.update(low.get("boards", []))
    return sorted(boards)


def live_fetchers() -> LiveFetchers:
    """The transport a scheduled run gets: httpx for three sources, a Chromium for oliveyoung.

    A function rather than a literal at the call site so a test can ask what the default is without
    running a collection, and so the browser profile directory has one place to move to when a
    deployment wants it somewhere other than `transport.DEFAULT_PROFILE_DIR`.
    """
    return LiveFetchers()


def run(
    dataset: str,
    board: str | None = None,
    since: str | None = None,
    *,
    database_url: str | None = None,
    fetcher: Fetcher | None = None,
    captured_at: datetime | None = None,
) -> int:
    """Collect one hour of `dataset`. Returns the exit code: 0 ok, 1 partial, 2 blocked.

    `since` is accepted for the entrypoint's shape (contracts/entrypoints.md) but unused by every
    dataset today -- every walk here starts from the current ranking, not a date range.
    """
    del since
    try:
        wanted = Dataset(dataset)
    except ValueError:
        known = ", ".join(d.value for d in Dataset)
        print(f"no dataset named {dataset!r}; known: {known}")
        return 2

    if board is not None and board not in review_low_boards():
        print(f"no review_low board named {board!r}; known: {', '.join(review_low_boards())}")
        return 2

    chosen = [cls() for cls in SOURCES.values() if wanted in cls.datasets]
    if not chosen:
        print(f"no registered source collects {wanted.value!r}")
        return 2

    when = captured_at or hour_bucket(datetime.now(UTC))
    engine = create_engine(database_url or storage_db.runtime_url())
    try:
        log = RunLog(engine)
        run_id = log.start(when, [s.key for s in chosen], [wanted.value])

        active_fetcher = fetcher or live_fetchers()
        journal = PostgresJournal(engine, run_id)

        try:
            report = collect(
                sources=chosen,
                dataset=wanted,
                sink=_EngineSink(engine),
                captured_at=when,
                fetcher=active_fetcher,
                journal=journal,
                board=board,
                # Only here. This is the entrypoint cron runs, and two cron lines overlapping on one
                # source is the whole reason the lock exists.
                lock=PostgresSourceLock(engine),
            )
        except BaseException as exc:
            log.finish(run_id, status="failed", note=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            # A Chromium is a process tree and an httpx client holds sockets; neither is reaped by
            # the run ending, and a caller that injected its own fetcher owns closing that one.
            if fetcher is None:
                active_fetcher.close()  # pyright: ignore[reportAttributeAccessIssue]

        log.record_sources(run_id, report.sources)
        status = "blocked" if report.blocked else ("ok" if report.ok else "partial")
        log.finish(run_id, status=status, note=_note(report))
        return exit_code_for(report)
    finally:
        # This returns rather than exits, so the pool outlives the run for any in-process caller --
        # and `trend_radar_runtime` is capped at 8 connections while a walk holds at most 3 (the
        # source lock plus the widest concurrency shipped, hwahae's 2). That cap is not set anywhere
        # in this repo; storage/locks.py records where it does come from and when it was read.
        engine.dispose()


def _note(report: RunReport) -> str | None:
    """Why this run is not a plain `ok`, in one line -- the only thing an unattended run says beyond
    its exit code, and the column `needs.collector_health` puts next to `status = 'partial'`."""
    parts = []
    if report.blocked:
        parts.append("blocked: " + ", ".join(report.blocked))
    if report.skipped:
        parts.append("skipped (locked by another run): " + ", ".join(report.skipped))
    return "; ".join(parts) or None


class _EngineSink:
    """Commits each batch in its own transaction as it arrives -- not one transaction for the whole
    run, so a crash partway through does not discard rows a natural-key upsert already made safe to
    have written twice.

    Taking the connection out of the pool per call is also how this satisfies `engine.Sink`'s
    requirement to be callable from several threads at once: the `Engine` is shared and thread-safe,
    a `Connection` would not be. Holding one open for the run would be fewer checkouts and a
    concurrency bug."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def write(self, records) -> None:
        with self._engine.begin() as connection:
            storage_db.write_records(connection, records)


__all__ = ["run", "review_low_boards"]
