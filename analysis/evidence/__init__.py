"""판정 셀을 받치는 소비자 발화의 선별 — `contracts/interfaces.md` §근거 가 정본이다 (포크 #6).

규칙의 출처는 ydc `analysis/slices/ydc/evidence_comments.py` 이고, 슬라이스를 import 하지 않고 옮겨 적었다
(`analysis/trend`·`analysis/judge` 가 쓴 방식). 이 모듈은 DB 를 모른다: 후보 목록을 받아 근거 행을 만들 뿐이라
같은 규칙이 저장된 코퍼스에서도 ydc 의 원 수집 CSV 에서도 돌고, 그 자리가 골든이 성립하는 자리다.

**근거는 검색이 아니다.** 이 파일에 순위 모델이 없는 것이 그 문장이다 — 어느 문서가 이 주제를 말했는지는
`corpus_mention` 이 이미 답했고, 여기 남은 일은 그중 무엇을 인용할지를 좋아요로 정하는 것뿐이다. 왜
`cosmai retrieval search` 로 대체하지 않는지와 그 실측은 계약 §근거 가 든다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from analysis.types import TopicQuarterEvidenceRow

# 선별의 정의 판본. `metric`·`judgement` 와 달리 합의 문서가 아니라 코드가 정한 규칙 넷의 판본이라
# `rule-vX.Y` 형식 그대로다 (`contracts/versioning.md`).
EVIDENCE_VERSION = "rule-v0.1"

# 셀당 몇 건을 남기는가. 카드 한 장에 들어가는 수이고, 025 의 CHECK 이 아니라 여기가 그 수의 자리다 --
# DDL 은 추가만이라 한번 적은 상한을 되돌릴 수 없다 (계약 §근거).
TOP_PER_CELL = 3

# 인용하지 않는 문서. 지표는 복붙을 `unique_ratio` 의 분모에 세지만(§수식) 세는 일과 인용은 다른 일이다.
QUOTABLE_FLAGS = ""


@dataclass(frozen=True)
class Candidate:
    """근거가 될 수 있는 (문서, 주제) 하나. 한 문서가 여러 주제에 걸리면 그만큼 후보가 된다."""

    doc_id: str
    quarter: str  # 부모 영상의 분기다 -- 댓글 자기 시각이 아니다 (코퍼스 규칙 3)
    topic_key: str
    source: str
    channel_id: str  # 댓글도 부모 영상의 채널을 싣는다 (023)
    like_count: int
    author_channel_hash: str
    quality_flags: str
    matched_term: str | None = None


def author_hash(channel_id: str) -> str:
    """수집기가 댓글 작성자 채널 ID 를 해시한 것과 같은 규칙(ydc `youtube_collector.py`).

    이 규칙이 수집기와 갈리면 제작자 댓글이 하나도 안 걸리고, 그 통과는 조용하다 -- 근거가 소비자
    발화가 아니게 되는데 산출물은 그대로 그럴듯하다.
    """
    return hashlib.sha256(f"youtube:{channel_id}".encode()).hexdigest()[:24]


def is_creator(candidate: Candidate) -> bool:
    """그 영상 채널 본인의 댓글인가. 좋아요 상위가 대부분 고정 댓글이라 소비자 발화가 아니다."""
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
    """후보 전부를 받아 셀마다 상위 `top` 건을 근거 행으로. `cells` 는 판정된 (주제, 분기, source) 다.

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
