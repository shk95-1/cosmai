"""`cosmai collect commerce` -- wired for #7. Real network transport is #10's job ("라이브 수집 없음"
here); `fetcher` is the seam #10 plugs a real `Fetcher` into, and tests plug a fixture-backed fake into.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce import sources as _sources  # noqa: F401 -- import registers every source
from collectors.commerce.contract import Payload
from collectors.commerce.engine import Fetcher, collect, exit_code_for
from collectors.commerce.models import Dataset, hour_bucket
from collectors.commerce.registry import SOURCES
from collectors.commerce.storage import db as storage_db
from collectors.commerce.storage.db import PostgresJournal, RunLog, create_engine

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


class _RaisingFetcher:
    """The default fetcher: fails loudly rather than opening a real socket. #10 replaces this."""

    def fetch(self, fetch: object) -> Payload:  # pragma: no cover - exercised only if actually called
        raise NotImplementedError(
            "collectors.commerce has no live transport yet; see issue #10 (cutover). "
            "Tests inject a fixture-backed fetcher instead of calling the CLI's default."
        )


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
    log = RunLog(engine)
    run_id = log.start(when, [s.key for s in chosen], [wanted.value])

    active_fetcher = fetcher or _RaisingFetcher()
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
        )
    except BaseException as exc:
        log.finish(run_id, status="failed", note=f"{type(exc).__name__}: {exc}")
        raise

    log.record_sources(run_id, report.sources)
    status = "blocked" if report.blocked else ("ok" if report.ok else "partial")
    log.finish(run_id, status=status, note=", ".join(report.blocked) or None)
    return exit_code_for(report)


class _EngineSink:
    """Commits each batch in its own transaction as it arrives -- not one transaction for the whole
    run, so a crash partway through does not discard rows a natural-key upsert already made safe to
    have written twice."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def write(self, records) -> None:
        with self._engine.begin() as connection:
            storage_db.write_records(connection, records)


__all__ = ["run", "review_low_boards"]
