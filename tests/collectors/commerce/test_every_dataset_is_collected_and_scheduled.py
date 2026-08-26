"""origin: playbook/snippets/test_every_enum_member_is_collected.py (service/trend-radar
tests/test_every_dataset_has_a_collector.py). A member with a collector and no schedule is a real
recorded outage (playbook 02-test-discipline.md T10), so every commerce Dataset gets both -- and where
contracts/entrypoints.md §스케줄 names a time, stack/crontab.d has to actually run at it (review round 1,
#7: review_stats drifted to 04:45 with nothing checking it)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import collectors.commerce.sources  # noqa: F401 -- registers every source
from collectors.commerce.models import Dataset
from collectors.commerce.registry import SOURCES
from collectors.commerce.storage.db import MAX_CONCURRENT_LANES

REPO_ROOT = Path(__file__).resolve().parents[3]
CRONTAB_D = REPO_ROOT / "stack" / "crontab.d"
ENTRYPOINTS_MD = REPO_ROOT / "contracts" / "entrypoints.md"


def crontab_text() -> str:
    """Every scheduled line in the stack, whichever container's file it sits in.

    stack/crontab became stack/crontab.d/<compose service> when the schedule was split across
    supercronic containers. Reading the whole directory rather than one named file is what keeps
    moving a line between containers from silently emptying these checks.
    """
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(CRONTAB_D.iterdir()) if p.is_file())


def _cron_times_by_dataset(text: str) -> dict[str, tuple[str, ...]]:
    """`{dataset: (minute, hour, dom, month, dow)}` for every `cosmai collect commerce --dataset X`
    line in `text` -- works on both a stack/crontab.d file and the fenced block in entrypoints.md's §스케줄,
    since both spell a cron line the same way."""
    out: dict[str, tuple[str, ...]] = {}
    for raw in text.splitlines():
        ln = raw.split("#", 1)[0].strip()
        if "cosmai collect commerce" not in ln or "--dataset" not in ln:
            continue
        parts = ln.split()
        out[parts[parts.index("--dataset") + 1]] = tuple(parts[:5])
    return out


def _entrypoints_schedule_block() -> str:
    text = ENTRYPOINTS_MD.read_text(encoding="utf-8")
    start = text.index("## 스케줄")
    fence_start = text.index("```", start) + 3
    fence_end = text.index("```", fence_start)
    return text[fence_start:fence_end]


CONTRACT_TIMES = _cron_times_by_dataset(_entrypoints_schedule_block())
CRONTAB_TIMES = _cron_times_by_dataset(crontab_text())


def test_there_are_sources_and_datasets():
    assert SOURCES and list(Dataset)


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_some_source_collects_this_dataset(dataset: Dataset):
    collectors = sorted(k for k, cls in SOURCES.items() if dataset in cls.datasets)
    assert collectors, f"no source declares {dataset.value!r}: the row count is zero forever"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_collector_of_it_has_a_seed(dataset: Dataset):
    seedless = sorted(k for k, cls in SOURCES.items() if dataset in cls.datasets and not cls().seeds(dataset))
    assert not seedless, f"{seedless} declare {dataset.value!r} and enqueue nothing"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_dataset_is_scheduled_in_the_commerce_crontab(dataset: Dataset):
    lines = [ln for ln in crontab_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert any("cosmai collect commerce" in ln and f"--dataset {dataset.value}" in ln for ln in lines), (
        f"{dataset.value} has a collector and no cron line"
    )


def test_the_contract_names_a_time_for_every_dataset():
    # If entrypoints.md §스케줄 stops naming a dataset's time, this fails loudly instead of letting
    # the crontab-time check below silently start covering fewer datasets than it should.
    named = set(CONTRACT_TIMES)
    all_commerce = {d.value for d in Dataset}
    assert named == all_commerce, (
        f"contracts/entrypoints.md §스케줄 names times for {sorted(named)}, "
        f"but every commerce dataset is {sorted(all_commerce)}"
    )


@pytest.mark.parametrize("dataset", sorted(CONTRACT_TIMES), ids=lambda d: d)
def test_crontab_time_matches_the_contract(dataset: str):
    assert dataset in CRONTAB_TIMES, f"{dataset} is scheduled in the contract but not in stack/crontab.d"
    assert CRONTAB_TIMES[dataset] == CONTRACT_TIMES[dataset], (
        f"stack/crontab.d schedules {dataset} at {' '.join(CRONTAB_TIMES[dataset])}, "
        f"contracts/entrypoints.md §스케줄 says {' '.join(CONTRACT_TIMES[dataset])}"
    )


# --- schedule gap -------------------------------------------------------------------------------
# What replaced the old "no daily walk starts on minute 0" check (#10 §A-8-2). That check passed no
# matter how long a walk actually took, because the runtime it was reasoning about ("~74s") lived in
# a comment instead of being computed -- so when collectors/commerce started really enforcing its
# rate policy, the contract's numbers went stale and nothing went red. Everything below derives the
# runtime from the same SourcePolicy constants the engine paces against, so a constant change moves
# the assertion with it.

DAY_S = 86_400


@dataclass(frozen=True, slots=True)
class _Line:
    """One `cosmai collect commerce` cron line, reduced to what a gap check needs."""

    dataset: str
    board: str | None
    minute: int
    hour: int | None  # None == every hour, i.e. the `0 * * * *` ranking line.

    @property
    def starts(self) -> tuple[int, ...]:
        """Seconds past midnight at which this line fires, over one day."""
        hours = range(24) if self.hour is None else (self.hour,)
        return tuple(h * 3600 + self.minute * 60 for h in hours)


def _commerce_cron_lines(text: str) -> tuple[_Line, ...]:
    lines: list[_Line] = []
    for raw in text.splitlines():
        ln = raw.split("#", 1)[0].strip()
        if "cosmai collect commerce" not in ln or "--dataset" not in ln:
            continue
        parts = ln.split()
        minute, hour, dom, month, dow = parts[:5]
        # Only a daily-or-hourly line has a start time this model can place on a 24h timeline; a
        # monthly or stepped line would silently be treated as daily, so refuse it instead.
        assert (dom, month, dow) == ("*", "*", "*"), f"unsupported cron day fields in {ln!r}"
        assert minute.isdigit() and (hour == "*" or hour.isdigit()), f"unsupported cron time in {ln!r}"
        board = parts[parts.index("--board") + 1] if "--board" in parts else None
        lines.append(
            _Line(
                dataset=parts[parts.index("--dataset") + 1],
                board=board,
                minute=int(minute),
                hour=None if hour == "*" else int(hour),
            )
        )
    return tuple(lines)


def _paced_seconds(policy, requests: int) -> float:
    """Wall clock one source spends on `requests` requests, from its own policy constants.

    A token bucket at one request per `min_interval_s` with `burst` free at the start, which is why
    a walk of one seed costs nothing. `concurrency` overlaps waiting on responses but every lane
    draws from that one bucket, so it never divides the total -- the pace does all the bounding.

    This is the pace the policy *declares*, not a bound on the walk. `Gate._back_off` widens the
    live interval up to `Gate.MAX_INTERVAL_S` (300s) whenever the site answers 403/429/503, so a
    refused source walks slower than any number here -- daisomall's 30s becomes 300s. Response
    latency and retries are not priced either. Every number below is therefore a lower bound, which
    is the honest direction: a gap that fails here fails in production too.
    """
    return max(0, requests - policy.burst) * policy.min_interval_s


def _run_seconds(line: _Line, *, capped: bool) -> float:
    """How long that cron line runs. `capped=False` is the seed floor (every source walks exactly
    the requests `seeds()` hands it, nothing followed); `capped=True` is the budget tier, where
    every source spends its whole `max_requests_per_run`.

    Sources are **maxed, not summed** (#25): `engine.collect` walks them as parallel lanes, so a line
    costs its slowest source rather than all of them added up. Two floors, and the line pays the
    higher: the slowest single source, and the whole line's work spread evenly over the lanes it is
    allowed -- `MAX_CONCURRENT_LANES` is a connection budget (collectors/commerce/storage/db.py), so
    a dataset with more sources than lanes queues the surplus. Neither is a schedule: a real run
    hands lanes out in registry order, not longest-first, so it can only take longer than this.

    The budget tier is not a ceiling either. Besides the widening in `_paced_seconds`, a source with
    `max_requests_per_run=None` has no budget to charge, so it is priced at its seed count while
    `max_depth` lets it follow further -- all four declare one since #10, but the branch stays
    because a new source is not obliged to. Read the tier as "at least this long", never "at most".
    """
    dataset = Dataset(line.dataset)
    lanes = []
    for key in sorted(SOURCES):
        cls = SOURCES[key]
        if dataset not in cls.datasets:
            continue
        requests = len(cls().seeds(dataset, board=line.board))
        budget = cls.policy.max_requests_per_run
        if capped and budget is not None:
            requests = max(requests, budget)
        lanes.append(_paced_seconds(cls.policy, requests))
    if not lanes:
        return 0.0
    at_once = min(len(lanes), MAX_CONCURRENT_LANES)
    return max(max(lanes), sum(lanes) / at_once)


CRON_LINES = _commerce_cron_lines(crontab_text())
# Every start time in one day, in order -- the hourly ranking line contributes 24 of them.
OCCURRENCES: tuple[tuple[int, _Line], ...] = tuple(
    sorted(((start, line) for line in CRON_LINES for start in line.starts), key=lambda p: p[0])
)

# Adjacencies the capped tier cannot satisfy, as (running, next to start). They are not a crontab bug
# to be nudged away and they are not waiting on anything: at its request budget the hourly ranking
# walk owns close to half of every hour, which leaves no honest slot for five daily walks. The
# per-source advisory lock (#10 §A-8-1) is what makes the overlap harmless and it has landed --
# collectors/commerce/storage/locks.py, wired into the cron entrypoint and held there by
# tests/collectors/commerce/test_source_lock.py. Nothing here queries it: interval arithmetic cannot
# see a lock, so these two overlap on this file's terms permanently.
BUDGET_OVERLAPS_THE_LOCK_CLOSES = frozenset({("ranking", "product"), ("ranking", "review")})


def _hhmm(start: int) -> str:
    return f"{start // 3600:02d}:{start % 3600 // 60:02d}"


def _overlaps(index: int, *, capped: bool) -> list[str]:
    """Occurrences that start before occurrence `index` is done, wrap-around included."""
    start, line = OCCURRENCES[index]
    runtime = _run_seconds(line, capped=capped)
    late = []
    for other_index, (other_start, other) in enumerate(OCCURRENCES):
        if other_index == index:
            continue
        delta = (other_start - start) % DAY_S
        if delta < runtime:
            late.append(f"{other.dataset} at {_hhmm(other_start)} (+{delta}s into a {runtime:.0f}s walk)")
    return late


def _gap_params(*, capped: bool) -> list:
    params = []
    for index, (start, line) in enumerate(OCCURRENCES):
        following = OCCURRENCES[(index + 1) % len(OCCURRENCES)][1]
        marks = []
        if capped and (line.dataset, following.dataset) in BUDGET_OVERLAPS_THE_LOCK_CLOSES:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason=(
                        f"{line.dataset} -> {following.dataset} overlaps at full budget and gap "
                        "arithmetic cannot close it; the per-source advisory lock does"
                    ),
                )
            )
        params.append(pytest.param(index, marks=marks, id=f"{line.dataset}@{_hhmm(start)}"))
    return params


@pytest.mark.parametrize("key", sorted(SOURCES), ids=lambda k: k)
def test_a_sources_pace_is_what_bounds_its_walk(key: str):
    # The gap checks below multiply min_interval_s; a source at 0 would contribute 0 seconds and
    # quietly make every schedule look clear, however many requests it fires.
    policy = SOURCES[key].policy
    assert policy.min_interval_s > 0, (
        f"{key} paces at {policy.min_interval_s}s across {policy.concurrency} lanes: with no interval "
        "its walk costs zero seconds here and the schedule gap check stops meaning anything"
    )


@pytest.mark.parametrize("index", _gap_params(capped=False))
def test_no_commerce_walk_starts_while_a_seed_paced_walk_is_running(index: int):
    """The tier that always runs: every line walking only its seeds, the least any run can cost.

    Split from the capped tier below because this one is a bound the crontab can actually honour --
    a failure here is a schedule to fix today. The capped tier is a bound only an advisory lock can
    honour, so failing it would say nothing about the times chosen.
    """
    start, line = OCCURRENCES[index]
    late = _overlaps(index, capped=False)
    assert not late, f"{line.dataset} at {_hhmm(start)} is still running when {'; '.join(late)} starts"


@pytest.mark.parametrize("index", _gap_params(capped=True))
def test_no_commerce_walk_starts_while_a_budget_capped_walk_is_running(index: int):
    """The tier that records what gap arithmetic cannot close: every line spending its whole budget.

    Two adjacencies overlap here and always will -- no daily time clears an hourly walk that owns
    half of every hour. What makes that harmless is the per-source advisory lock, not a schedule, and
    this file never queries it, so they are xfail(strict=True) as a standing record rather than a
    countdown to anything. Strict, not skipped, because the one thing that *would* close them on
    these terms -- a budget shrinking until the pair no longer overlaps -- has to turn them into
    XPASS failures and force this list to be trimmed. Every other adjacency is unmarked: a policy
    constant that grows until a new pair overlaps has to fail here loudly.
    """
    start, line = OCCURRENCES[index]
    late = _overlaps(index, capped=True)
    assert not late, f"{line.dataset} at {_hhmm(start)} is still running when {'; '.join(late)} starts"
