"""The selection of the consumer speech that holds up a verdict cell — `contracts/interfaces.md` §Evidence is
canonical (fork #6).

The rules come from ydc `evidence_comments.py` (shk95-1/cosmai-ydc-old `v0.1.0` `02440ab`; unchanged through
the import pin `v0.4.0`, `contracts/versioning.md`) and were written over rather than imported from the
pinned copy `analysis/slices/ydc/` (deleted, #9) (the way `analysis/trend` and `analysis/judge` did it).
This module knows no DB: it takes a
candidate list, so the same rules run on the stored corpus and on ydc's raw collection CSV, and that is where
the golden comparison stands.

**Evidence is not search.** That sentence is why there is no ranking model in this file — which documents
spoke of this topic has already been answered by `corpus_mention`, and all that is left here is deciding by
like count which of them to quote. Why it is not replaced by `cosmai retrieval search`, and the measurement
behind that, are carried by the contract's §Evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from analysis.types import TopicQuarterEvidenceRow

# The definition revision of the selection. Unlike `metric` and `judgement` it is the revision of four rules
# the code settles rather than an agreement document, so it keeps the `rule-vX.Y` form
# (`contracts/versioning.md`).
EVIDENCE_VERSION = "rule-v0.1"

# How many are kept per cell. It is the number that goes into one card, and this rather than 025's CHECK is
# that number's place -- the DDL is additive only, so a ceiling once written cannot be taken back
# (the contract's §Evidence).
TOP_PER_CELL = 3

# Documents that are not quoted. The metrics count copy-paste in `unique_ratio`'s denominator (§Formulas), but
# counting and quoting are different jobs.
QUOTABLE_FLAGS = ""


@dataclass(frozen=True)
class Candidate:
    """One (document, topic) that could be evidence. A document hitting several topics becomes that many
    candidates."""

    doc_id: str
    quarter: str  # the quarter of the parent video -- not the comment's own time (corpus rule 3)
    topic_key: str
    source: str
    channel_id: str  # a comment carries the parent video's channel as well (023)
    like_count: int
    author_channel_hash: str
    quality_flags: str
    matched_term: str | None = None


def author_hash(channel_id: str) -> str:
    """The same rule the collector used to hash a comment author's channel ID (ydc
    `youtube_collector.py`).

    If this rule drifts from the collector's, not one creator comment is caught, and that pass is quiet --
    the evidence stops being consumer speech while the output stays just as plausible.
    """
    return hashlib.sha256(f"youtube:{channel_id}".encode()).hexdigest()[:24]


def is_creator(candidate: Candidate) -> bool:
    """Is it a comment by the video's own channel. Most of the top-liked ones are pinned comments, which are
    not consumer speech."""
    return candidate.author_channel_hash == author_hash(candidate.channel_id)


def quotable(candidate: Candidate) -> bool:
    return candidate.quality_flags == QUOTABLE_FLAGS and not is_creator(candidate)


def _ladder(candidate: Candidate) -> tuple[int, str]:
    """Like count descending, ties by doc_id. Without the second key the read order decides the winner of a
    tie, and a stored table does not yield the same rows on a rerun (the contract's §Evidence: 32 of the
    fixture's 46 cells have a tie)."""
    return (-candidate.like_count, candidate.doc_id)


def select(
    candidates: Iterable[Candidate],
    *,
    run_id: int,
    scope: str,
    content_type: str,
    panel_version: int,
    panel_role: str,
    snapshot_id: int,
    cells: Collection[tuple[str, str, str]],
    top: int = TOP_PER_CELL,
) -> list[TopicQuarterEvidenceRow]:
    """Takes every candidate and turns the top `top` per cell into evidence rows. `cells` are the judged
    (topic, quarter, source).

    격자 밖 후보를 여기서 떨어뜨리는 것은 025 의 FK 가 거절할 행을 만들지 않기 위해서다 --
    `trend_use = false` 인 주제(`선크림`·`추천_재구매`)에는 판정 셀이 아예 없다.
    """
    known = set(cells)
    buckets: dict[tuple[str, str, str], list[Candidate]] = {}
    for candidate in candidates:
        key = (candidate.topic_key, candidate.quarter, candidate.source)
        if key not in known or not quotable(candidate):
            continue
        buckets.setdefault(key, []).append(candidate)

    made: list[TopicQuarterEvidenceRow] = []
    for (topic_key, quarter, source), found in sorted(buckets.items()):
        for rank, candidate in enumerate(sorted(found, key=_ladder)[:top], 1):
            made.append(
                TopicQuarterEvidenceRow(
                    run_id=run_id,
                    scope=scope,
                    topic_key=topic_key,
                    quarter=quarter,
                    source=source,
                    content_type=content_type,
                    panel_version=panel_version,
                    panel_role=panel_role,
                    rank=rank,
                    snapshot_id=snapshot_id,
                    doc_id=candidate.doc_id,
                    like_count=candidate.like_count,
                    matched_term=candidate.matched_term,
                )
            )
    made.sort(key=lambda row: (row.source, row.topic_key, row.quarter, row.rank))
    return made
