"""Which polarity implementation owns which `lexicon_category` **from which month** — this file is that
answer (#31, #97).

The natural key of 005, `(src, ref, need_key, extractor_version, md5(sentence))`, carries no
`polarity_version`. So when two implementations process the same sentence under the same extractor version,
**row-level ownership does not hold** — whichever runs later overwrites the earlier label with an in-place
upsert, and the delete statement of a run without a scope removes even what is left. Ownership is therefore
per `(scope, period)` rather than per row: only the owner writes and deletes the months from that scope's
`since` onwards, while the months before it and the scopes with no owner are refreshed by the rules as they
are today. Rows with `lexicon_category IS NULL` (comments and reviews without a category) have no owner in
any month.

The period is what separates registration from the pass. When the whole scope was handed over, registering
and deferring the pass meant no row was created at all for new reviews in that category (the rules skip them
before candidate extraction), so 26 categories had to wait for a 6-7 hour full pass. Registering with `since`
set to the next month lets the rules keep refreshing the past while the owner's pass only has to fill its own
period.

The value is exactly the `polarity_version` that implementation stamps on its output rows — that string is
the only clue a run has that a row is its own. When the revision goes up (few-shot, prompt date) this table
has to move with it, and the ownership-table check in tests/test_analyze_polarity.py catches that moment.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from analysis.polarity import VERSION as RULE_VERSION

__all__ = [
    "ALWAYS",
    "CRON_SINCE",
    "NO_OWNERS",
    "OWNERS",
    "Owner",
    "Scopes",
    "may_write",
    "owner_of",
    "scopes_of",
    "unready",
]


@dataclass(frozen=True)
class Owner:
    """The owner of one scope: the revision, and the first month that revision answers for (a YYYY-MM like
    `need_mention.month`)."""

    version: str
    since: str


Scopes = tuple[tuple[str, str], ...]  # (scope, since) pairs — the same shape on the way into SQL

ALWAYS = "0000-00"  # Smaller than any YYYY-MM — after a full pass every month of a scope is the owner's.

# 2026-08-24 홀드아웃에서 gemma4 가 규칙을 넘었다 (interfaces.md §LLM 실측). 선블록은 전량 패스(run 16,
# 6h44m)가 끝나 ALWAYS 다. 나머지 26개는 그 뒤로 오는 달만 주인의 몫이다 — 등록과 패스가 같은 순간일
# 필요가 없어졌으므로(#97) 26개를 한꺼번에 꺼내도 그 앞의 달은 05:00 규칙 줄이 계속 갱신한다.
_GEMMA4_2026_08_24 = "llm-ollama-gemma4:latest-fs2-20260824"
# The first month the cron line (`0 8` in `stack/crontab.d/analyze`) runs. **If the deployment slips past
# this month, move this value with it** — a month too late leaves the rules writing the months in between,
# and a month too early makes the first night carry that whole backlog at once.
CRON_SINCE = "2026-08"
# Exactly the target table of #33 — 26 entries by descending mention-row count (21,123 rows in total). The
# key is the `lexicon_category` that `category_map` produces: a leaf listed in that table is its mapped value
# and a leaf outside it is the identity (analysis/units.py).
_CRON_SCOPES = (
    "에센스",
    "쿠션",
    "시트팩",
    "클렌징폼",
    "크림",
    "헤어토닉/앰플",
    "헤어트리트먼트",
    "애프터선",
    # 선스틱은 #33 의 26개 목록이 쓰인 뒤에 생긴 카테고리다 — 운영 need_mention 에 588행(2026-08-26
    # 조정자 실측). 빠뜨리면 규칙이 계속 갱신해 조용히 남으므로 크론 대상에 넣는다.
    "선스틱",
    "립틴트",
    "뷰티/위생",
    "샴푸",
    "염모제",
    "클렌징워터",
    "스킨/토너",
    "페이셜미스트",
    "클렌징밀크",
    "패드",
    "프로틴음료(고형)",
    "올인원",
    "BB/CC",
    "블러셔",
    "립틴트/라커",
    "아이섀도우",
    "바디로션/크림",
    "스킨케어기기",
    "향수",
)
OWNERS: Mapping[str, Owner] = MappingProxyType(
    {"선블록": Owner(_GEMMA4_2026_08_24, ALWAYS)}
    | {scope: Owner(_GEMMA4_2026_08_24, CRON_SINCE) for scope in _CRON_SCOPES}
)
# Exactly the behaviour from before ownership — empty this table and a rule run writes and deletes all of it.
NO_OWNERS: Mapping[str, Owner] = MappingProxyType({})


def owner_of(owners: Mapping[str, Owner], lexicon_category: str | None, month: str) -> str | None:
    """The owning revision of this (scope, month) — None when there is none (= the rules, and any
    implementation not in the table)."""
    owner = owners.get(lexicon_category) if lexicon_category is not None else None
    return owner.version if owner is not None and month >= owner.since else None


def scopes_of(owners: Mapping[str, Owner], polarity_version: str, *, mine: bool) -> Scopes:
    """The (scope, since) pairs this classifier owns, or those someone else owns — three predicates take this
    array into SQL."""
    return tuple(
        sorted(
            (scope, owner.since)
            for scope, owner in owners.items()
            if (owner.version == polarity_version) == mine
        )
    )


def may_write(owners: Mapping[str, Owner], version: str, lexicon_category: str | None, month: str) -> bool:
    """May this revision write and delete this row — one home for the ownership predicate.

    The read skip in `analysis/polarity/pipeline.py` calls this as it is, and the delete statement and
    `DO UPDATE` use the `OWNED` predicate, the same meaning carried into SQL (it takes the same two
    (scope, since) arrays).
    """
    owner = owner_of(owners, lexicon_category, month)
    if owner is not None:
        return owner == version
    # A place with no owner belongs to the rules. A registered implementation never steps outside its own
    # (scope, period) — otherwise one scope-less line from an owner is a relabel of everything.
    return not any(registered.version == version for registered in owners.values())


def unready(owners: Mapping[str, Owner], version: str, scope: str | None) -> str | None:
    """Is this a place where an implementation other than the rules may be let loose by hand — or one line
    saying why not (cosmai/cli.py calls it).

    A line without a scope narrows to the implementation's own share only when it has a place in the table
    (#97) — for an implementation outside the table that same line is still a full relabel of the rule
    population, and the price is time or a GPU (which is why paid-or-not is not the criterion). A named scope
    with no owner yet is refused as well: the rules relabel an ownerless scope every day at 05:00, so a pass
    that runs without registering succeeds and is gone by the next dawn.
    A run that named someone else's scope is not filtered out here — that refusal is the step's job, and the
    contract promises it as a failed run + exit code 1 (contracts/entrypoints.md §Analysis).
    """
    if version == RULE_VERSION:
        return None
    if scope is not None:
        if scope not in owners:
            return (
                f"{scope} has no owner, so the 05:00 rule run relabels it tonight; register it to "
                f"{version} in analysis/polarity/ownership.py before this pass"
            )
        return None
    if not scopes_of(owners, version, mine=True):
        return (
            f"--impl {version} owns no scope, so this would relabel every scope; register it with a "
            "since month in analysis/polarity/ownership.py, or name one with --scope <category>"
        )
    return None
