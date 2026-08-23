"""The watch list `cosmai collect youtube --dataset watch` reads: one typed directive per line.

```
video             dQw4w9WgXcQ
channel           @director_pihyunjung
channel+comments  @beauty_channel
search            kpop debut
search+comments   화장품
playlist          UU5oM4Ai05dQqiVL6rypAo_A
trending          KR
```

origin: service/yt-scrapper/src/tubedepth/watchlist.py -- ported for #8 verbatim except DIRECTIVES,
which now names `video.transcript` as a follow-up alongside `video.metadata` on every listing directive
(issue #8 "transcript follow-up 복구"): the original table never listed `video.transcript` as any
directive's follow-up at all, so nothing `watch` ever produced could reach it -- confirmed by reading
the code and the fixture-tested `_HANDLERS` dispatch in the archived flatten.py, which already had a
working `video.transcript` handler with no job kind ever queued to feed it. `video`-kind lines are
already a video target, not a listing, so they keep no follow-ups.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class WatchlistError(ValueError):
    """A watch list line this parser refuses -- an unknown directive or an empty target."""


@dataclass(frozen=True, slots=True)
class Directive:
    kind: str
    target: str
    follow_ups: tuple[str, ...]
    line: int


DIRECTIVES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "video": ("video.metadata", ()),
    "channel": ("channel.videos", ("video.metadata", "video.transcript")),
    "channel+comments": ("channel.videos", ("video.metadata", "video.transcript", "video.comments")),
    "search": ("search.videos", ("video.metadata", "video.transcript")),
    "search+comments": ("search.videos", ("video.metadata", "video.transcript", "video.comments")),
    "playlist": ("playlist.items", ("video.metadata", "video.transcript")),
    "playlist+comments": ("playlist.items", ("video.metadata", "video.transcript", "video.comments")),
    "trending": ("trending.videos", ("video.metadata", "video.transcript")),
}

COMMENT = "#"


def read_watchlist(path: Path) -> list[Directive]:
    """Parse the whole file, or refuse the whole file -- a typo queues nothing rather than everything
    above the typo, which is the harder failure to notice."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise WatchlistError(f"cannot read the watch list at {path}: {error}") from error

    directives: list[Directive] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT):
            continue
        directives.append(_directive(stripped, path=path, number=number))
    return directives


def _directive(line: str, *, path: Path, number: int) -> Directive:
    parts = line.split(maxsplit=1)
    name = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    kind_and_follow_up = DIRECTIVES.get(name.lower())
    if kind_and_follow_up is None:
        known = ", ".join(DIRECTIVES)
        raise WatchlistError(f"{path} line {number}: unknown directive {name!r} — known: {known}")
    target = rest.strip()
    if not target:
        raise WatchlistError(f"{path} line {number}: {name!r} names nothing to collect")
    kind, follow_ups = kind_and_follow_up
    return Directive(kind=kind, target=target, follow_ups=follow_ups, line=number)


__all__ = ["Directive", "DIRECTIVES", "WatchlistError", "read_watchlist"]
