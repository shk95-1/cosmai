"""`cosmai collect youtube` can be pointed at one channel from the command line (#183 fix round).

The transport shipped with `read_roster`, `roster_url` and `watchlist_path` as `run()` keyword
arguments and no `add_argument` behind any of them, so the only expressible `watch` was **all of
it**: 43 active panel rows, 129 listing jobs, and a `work` that then fans out the whole panel
bounded only by `MAX_QUEUE_DEPTH`. The first live run of a brand-new transport is exactly the run
that must be small, and it could only be made by importing `collectors.youtube.cli` in a Python
shell and calling its `run` by hand -- not a thing an operator does at 3am, and not a thing a
runbook can record.

The call is described rather than written out on purpose: `tests/tool/test_serial_group.py` finds
the tests that take the commerce source lock by searching a test file for that literal, and this
file takes no such lock. Spelling it here would either fail that guard or, worse, get the guard
widened until it lets prose through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cosmai.cli import build_parser, main


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def test_the_two_flags_exist_on_collect():
    args = _parse(["collect", "youtube", "--dataset", "watch", "--watchlist", "/x/one.txt", "--no-roster"])
    assert args.watchlist == "/x/one.txt"
    assert args.no_roster is True


def test_they_default_to_the_production_shape():
    """Absent flags must mean what cron means: the panel roster, and the checked-in file."""
    args = _parse(["collect", "youtube", "--dataset", "watch"])
    assert args.watchlist is None
    assert args.no_roster is False


def test_the_flags_reach_run_as_the_arguments_it_takes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    seen: dict[str, Any] = {}
    watchlist = tmp_path / "one-channel.txt"
    watchlist.write_text("channel+comments UCaaaaaaaaaaaaaaaaaaaaaa\n")

    def _fake_run(dataset: str, **kwargs: Any) -> int:
        seen["dataset"] = dataset
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("collectors.youtube.cli.run", _fake_run)
    assert (
        main(["collect", "youtube", "--dataset", "watch", "--watchlist", str(watchlist), "--no-roster"]) == 0
    )

    assert seen["dataset"] == "watch"
    assert seen["watchlist_path"] == watchlist
    assert seen["read_roster"] is False


def test_without_the_flags_the_roster_is_read_and_the_default_file_is_used(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def _fake_run(dataset: str, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("collectors.youtube.cli.run", _fake_run)
    assert main(["collect", "youtube", "--dataset", "watch"]) == 0
    assert seen["read_roster"] is True
    assert seen["watchlist_path"] is None


def test_the_other_two_collectors_are_not_handed_youtube_arguments(monkeypatch: pytest.MonkeyPatch):
    """The flags sit on the shared `collect` parser, the way `--board` does for commerce. They must
    not leak into a `run()` that has no such parameter."""
    seen: dict[str, Any] = {}

    def _fake_run(dataset: str, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("collectors.naver.cli.run", _fake_run)
    assert main(["collect", "naver", "--dataset", "datalab", "--no-roster"]) == 0
    assert "read_roster" not in seen
    assert "watchlist_path" not in seen


def test_the_help_text_says_which_collector_they_belong_to():
    """`--board` next to them is labelled 'commerce review_low only'; an unlabelled flag on a shared
    parser reads as applying to all three."""
    help_text = build_parser().parse_args(["collect", "youtube", "--dataset", "watch"]) and _help()
    assert "youtube watch only" in help_text


def _help() -> str:
    import contextlib
    import io

    parser = build_parser()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        parser.parse_args(["collect", "--help"])
    return buffer.getvalue()
