"""선언된 단계가 크론탭과 어긋나지 않는다 (#138).

`db/seed/pipeline.STAGES` 가 기대 주기의 정본이지만, 실제로 도는 것은 `stack/crontab.d/` 다.
둘이 갈리면 관제 화면이 조용히 틀린 답을 한다 — 그것을 여기서 막는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from db.seed.pipeline import STAGES

CRONTAB_DIR = Path(__file__).resolve().parents[1] / "stack" / "crontab.d"

# `cosmai collect <arm> --dataset <ds>` 와 `cosmai analyze <ds>` 두 모양뿐이다.
COLLECT = re.compile(r"cosmai\s+collect\s+(\S+)\s+--dataset\s+(\S+)")
ANALYZE = re.compile(r"cosmai\s+analyze\s+(\S+)")


def cron_lines() -> list[tuple[str, str]]:
    """(크론 표현식, 명령) 목록. 주석과 빈 줄은 뺀다."""
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
    """크론 줄의 명령에서 stage_key 를 뽑는다.

    analyze 는 두 줄이 같은 하위명령을 쓰지 않는다는 보장이 없다 -- `polarity` 증분 패스는
    `--missing` 으로 갈리고 analysis_run.note 도 그 어휘로 갈린다(contracts/entrypoints.md).
    """
    if m := COLLECT.search(command):
        return f"{m.group(1)}:{m.group(2)}"
    m = ANALYZE.search(command)
    assert m, f"알 수 없는 크론 명령: {command}"
    sub = m.group(1)
    return f"analyze:{sub}_missing" if "--missing" in command else f"analyze:{sub}"


def interval_of(expr: str) -> str:
    """크론 표현식이 뜻하는 기대 주기. 지금 쓰는 네 모양만 안다 -- 새 모양이 오면 여기서 죽는다."""
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
    # 지금 profile 뒤에 있는 것은 하나뿐이다(STATE.md §2). 더 늘면 그 사실을 여기서 마주친다.
    off = {s.stage_key for s in STAGES if not s.enabled}
    assert off == {"youtube:watch"}, off
