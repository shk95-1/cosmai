"""판정 셀을 받치는 소비자 발화의 선별 — `contracts/interfaces.md` §근거 가 정본이다 (포크 #6).

The rules come from ydc `evidence_comments.py` (shk95-1/cosmai-ydc-old `v0.1.0` `02440ab`; unchanged through
the import pin `v0.4.0`, `contracts/versioning.md`) and were written over rather than imported from the
pinned copy `analysis/slices/ydc/` (deleted, #9) (the way `analysis/trend` and `analysis/judge` did it).
This module knows no DB: it takes a
candidate list, so the same rules run on the stored corpus and on ydc's raw collection CSV, and that is where
the golden comparison stands.

**근거는 검색이 아니다.** 이 파일에 순위 모델이 없는 것이 그 문장이다 — 어느 문서가 이 주제를 말했는지는
`corpus_mention` 이 이미 답했고, 여기 남은 일은 그중 무엇을 인용할지를 좋아요로 정하는 것뿐이다. 왜
`cosmai retrieval search` 로 대체하지 않는지와 그 실측은 계약 §근거 가 든다.
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

# 셀당 몇 건을 남기는가. 카드 한 장에 들어가는 수이고, 025 의 CHECK 이 아니라 여기가 그 수의 자리다 --
# DDL 은 추가만이라 한번 적은 상한을 되돌릴 수 없다 (계약 §근거).
TOP_PER_CELL = 3

# 인용하지 않는 문서. 지표는 복붙을 `unique_ratio` 의 분모에 세지만(§수식) 세는 일과 인용은 다른 일이다.
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
    """좋아요 내림차순, 동점은 doc_id. 2차 키가 없으면 동점의 승자를 읽기 순서가 정하고, 저장되는 표는
    재실행이 같은 행을 내지 않는다 (계약 §근거: 픽스처 46셀 중 32셀에 동점이 있다)."""
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
