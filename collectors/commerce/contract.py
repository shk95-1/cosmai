"""The whole common interface. A source is `seeds` + `parse`, and nothing else.

origin: service/trend-radar/src/trend_radar/contract.py -- ported for #7. Everything a scraper usually
re-implements per site -- retries, pacing, the crawl frontier, persistence -- belongs to the engine. What
is left for a source is a pure function from bytes to records, which is why every site-specific line in
this package can be tested offline against a saved fixture.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable

from collectors.commerce.models import Dataset, Record

# What this crawler calls itself, not a browser string -- so a site operator reading an access log
# can tell who this is and ask it to stop.
DEFAULT_UA = "cosmai-commerce/0.1 (+https://github.com/slopindustries/cosmai)"


class Transport(StrEnum):
    HTTP = "http"
    BROWSER = "browser"


@dataclass(frozen=True, slots=True)
class Fetch:
    """One request the engine owes a source.

    Hashable by value on purpose: the frontier dedupes by putting these in a set, so a ranking page
    that links the same product twice costs one request rather than two. `headers` and `context` are
    tuples of pairs rather than dicts for the same reason -- a dict would make this unhashable.
    """

    url: str
    dataset: Dataset
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None
    transport: Transport | None = None  # None -> the source's policy default
    wait_for: str | None = None  # browser only: CSS selector to await
    click_before: str | None = None  # browser only: clicked before wait_for, if not already visible
    context: tuple[tuple[str, str], ...] = ()  # parser hints: category, page, parent key
    depth: int = 0

    def ctx(self, name: str, default: str | None = None) -> str | None:
        return dict(self.context).get(name, default)


@dataclass(frozen=True, slots=True)
class Payload:
    """What came back. The only thing a parser is allowed to look at."""

    fetch: Fetch
    status: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]
    elapsed_ms: int
    # Stamped by the engine from the run's hour bucket. A parser must never reach for the clock itself.
    captured_at: datetime | None = None

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> object:
        return json.loads(self.body)


@dataclass(frozen=True, slots=True)
class Yield:
    """What a parser produced: rows to keep, and requests to follow."""

    records: Sequence[Record] = ()
    follow: Sequence[Fetch] = ()


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """Fixed request and failure bounds for one source. A ceiling and a starting point, never a promise."""

    min_interval_s: float
    concurrency: int
    burst: int = 1
    timeout_s: float = 20.0
    max_attempts: int = 3
    transport: Transport = Transport.HTTP
    user_agent: str = DEFAULT_UA
    max_depth: int = 2
    max_requests_per_run: int | None = None

    def __post_init__(self) -> None:
        if self.min_interval_s < 0:
            raise ValueError("min_interval_s cannot be negative")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1; a lane with no workers collects nothing")
        if self.burst < 1:
            raise ValueError("burst must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_requests_per_run is not None and self.max_requests_per_run < 1:
            raise ValueError("max_requests_per_run must be at least 1 when set")


# How much of a site one run walks, as a source declares it. Stored with every run because a row's
# meaning depends on it -- a row count that changed between two hours is a site that changed or a scope
# that did, and only one of those is our doing.
Scope = Mapping[Dataset, Mapping[str, int]]


def narrowed(scope: Scope, datasets: Iterable[Dataset]) -> dict[str, dict[str, int]]:
    """The part of a source's scope that these datasets describe. Plain dicts: the column is jsonb."""
    return {d.value: dict(scope[d]) for d in datasets if d in scope}


@runtime_checkable
class Source(Protocol):
    key: ClassVar[str]
    policy: ClassVar[SourcePolicy]
    datasets: ClassVar[frozenset[Dataset]]
    scope: ClassVar[Scope]

    # `board` only means anything to a source that declares REVIEW_LOW (oliveyoung, #7); every other
    # source ignores it. Part of the shared signature anyway so the engine can call it uniformly.
    def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]: ...

    def parse(self, payload: Payload) -> Yield: ...
