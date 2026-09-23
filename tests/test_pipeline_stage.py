"""A declared stage does not diverge from the crontab (#138).

`db/seed/pipeline.STAGES` is canonical for the expected period, but what really runs is `stack/crontab.d/`.
When the two part, the control screen answers wrong quietly -- that is what is stopped here.
"""

from __future__ import annotations

import re
from pathlib import Path

from db.seed.pipeline import STAGES as UPSTREAM_STAGES
from db.seed.pipeline_corpus import STAGES as FORK_STAGES

CRONTAB_DIR = Path(__file__).resolve().parents[1] / "stack" / "crontab.d"

# Both declaration modules, because the crontab is one file set and cannot be held against half of it.
# `pipeline_corpus.py` is the fork's share of the same two tables (fork #94) and stayed out of this
# test while every row in it was `enabled=False` with no cron line; fork #95's `project:corpus` has a
# line, so leaving it out would report that line as "in cron but not declared".
STAGES = UPSTREAM_STAGES + FORK_STAGES

# Four command shapes: `cosmai collect <arm> --dataset <ds>`, `cosmai analyze <ds>`, `cosmai project <ds>`
# and `cosmai match <ds>` (fork #96's gated live chain, named by its first stage).
COLLECT = re.compile(r"cosmai\s+collect\s+(\S+)\s+--dataset\s+(\S+)")
ANALYZE = re.compile(r"cosmai\s+analyze\s+(\S+)")
PROJECT = re.compile(r"cosmai\s+project\s+(\S+)")
MATCH = re.compile(r"cosmai\s+match\s+(\S+)")


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
    if m := PROJECT.search(command):
        return f"project:{m.group(1)}"
    if m := MATCH.search(command):
        return f"match:{m.group(1)}"
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
    # A deliberately gated stage may omit its cron line. Every current upstream stage is active,
    # including youtube:watch since #39 closed; this also catches a missing cron line.
    gated = {s.stage_key for s in STAGES if not s.enabled}
    from_cron = {stage_key_of(cmd) for _, cmd in cron_lines()} | gated
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


def test_every_upstream_stage_is_enabled():
    # watch has run hourly since #39; keeping its old disabled declaration hides live failures.
    # The fork's disabled rows are answered by tests/test_archive_lineage.py.
    off = {s.stage_key for s in UPSTREAM_STAGES if not s.enabled}
    assert not off, off
