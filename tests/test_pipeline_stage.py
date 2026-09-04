"""A declared stage does not diverge from the crontab (#138).

`db/seed/pipeline.STAGES` is canonical for the expected period, but what really runs is `stack/crontab.d/`.
When the two part, the control screen answers wrong quietly -- that is what is stopped here.
"""

from __future__ import annotations

import re
from pathlib import Path

from db.seed.pipeline import STAGES

CRONTAB_DIR = Path(__file__).resolve().parents[1] / "stack" / "crontab.d"

# There are only the two shapes `cosmai collect <arm> --dataset <ds>` and `cosmai analyze <ds>`.
COLLECT = re.compile(r"cosmai\s+collect\s+(\S+)\s+--dataset\s+(\S+)")
ANALYZE = re.compile(r"cosmai\s+analyze\s+(\S+)")


def cron_lines() -> list[tuple[str, str]]:
    """The list of (cron expression, command). Comments and blank lines are dropped."""
    out: list[tuple[str, str]] = []
    for path in sorted(CRONTAB_DIR.iterdir()):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split(maxsplit=5)
            out.append((" ".join(fields[:5]), fields[5]))
    return out


def stage_key_of(command: str) -> str:
    """Takes the stage_key out of the command of a cron line.

    There is no guarantee two analyze lines do not use the same subcommand -- the `polarity` incremental pass
    is told apart by `--missing`, and analysis_run.note parts on the same vocabulary
    (contracts/entrypoints.md).
    """
    if m := COLLECT.search(command):
        return f"{m.group(1)}:{m.group(2)}"
    m = ANALYZE.search(command)
    assert m, f"알 수 없는 크론 명령: {command}"
    sub = m.group(1)
    return f"analyze:{sub}_missing" if "--missing" in command else f"analyze:{sub}"


def interval_of(expr: str) -> str:
    """The expected period a cron expression means. It knows only the four shapes in use -- a new shape dies
    here."""
    minute, hour, dom, _month, _dow = expr.split()
    if "/" in minute:
        return f"{minute.split('/')[1]} min"
    if hour == "*":
        return "1 hour"
    if dom != "*":
        return "1 mon"
    return "1 day"


def test_every_cron_line_is_declared():
    declared = {s.stage_key for s in STAGES}
    from_cron = {stage_key_of(cmd) for _, cmd in cron_lines()}
    assert from_cron - declared == set(), "크론에 있는데 선언되지 않은 단계"
    assert declared - from_cron == set(), "선언됐는데 크론에 없는 단계"


def test_declared_interval_matches_the_cron_expression():
    by_key = {stage_key_of(cmd): interval_of(expr) for expr, cmd in cron_lines()}
    mismatched = {
        s.stage_key: (s.expected_interval, by_key[s.stage_key])
        for s in STAGES
        if s.stage_key in by_key and s.expected_interval != by_key[s.stage_key]
    }
    assert not mismatched, f"선언과 크론이 어긋난다 (선언, 크론): {mismatched}"


def test_only_youtube_watch_is_disabled():
    # Only one thing sits behind a profile today (STATE.md §2). If more appear, that fact is met here.
    off = {s.stage_key for s in STAGES if not s.enabled}
    assert off == {"youtube:watch"}, off
