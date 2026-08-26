"""매니페스트의 `reproduces` 세 숫자를 반입분 위에서 다시 센다 (포크 #4 의 2번).

이 세 숫자는 EPIC 이 "cosmai 위에서 같은 뜻의 숫자가 나오는가"를 묻는 대조군이다. 여기서 하는 일은
**같은 정의를 SQL 로 다시 쓰는 것**이지 숫자를 맞추는 것이 아니다 -- 어긋나면 반입이 틀렸거나 계약
해석이 틀린 것이고, 둘 다 고칠 곳은 이 함수가 아니다.

정의(manifest.reproduces.재현_방법):
  document 에서 content_type=video_long 이고 channel.panel_role=product 이며 mention 에
  topic_id=선크림 이 있는 문서 -> 964. 그 영상들을 parent_item_id 로 갖는 댓글 -> 60,348.
  그중 quality_flags 가 빈 것 -> 60,311.
"""

from __future__ import annotations

from typing import Any, LiteralString

import psycopg

from db.corpus import active_snapshot
from db.seed import panel

TOPIC_ID = "선크림"
PANEL_ROLE = "product"
LONG_FORM = "video_long"

# 패널은 언제나 `panel_channel` 의 활성 판본을 거쳐 읽는다 -- 맨 `WHERE active` 는 활성 판본이 둘일 때
# 분모를 조용히 두 배로 만든다(포크 #4 본문, #31 리뷰). 판본 번호는 `panel.active_version` 이 고른다.
REPRODUCE_SQL: LiteralString = """
WITH videos AS (
  SELECT d.source_item_id
  FROM corpus_document d
  JOIN panel_channel p
    ON p.channel_id = d.channel_id AND p.version = %(panel_version)s AND p.active
  WHERE d.snapshot_id = %(snapshot_id)s
    AND d.content_type = %(long_form)s
    AND p.panel_role = %(panel_role)s
    AND EXISTS (
      SELECT 1 FROM corpus_mention m
      WHERE m.snapshot_id = d.snapshot_id AND m.doc_id = d.doc_id AND m.topic_id = %(topic_id)s
    )
), comments AS (
  SELECT c.quality_flags
  FROM corpus_document c
  JOIN videos v ON v.source_item_id = c.parent_item_id
  WHERE c.snapshot_id = %(snapshot_id)s AND c.content_type = 'comment'
)
SELECT
  (SELECT count(*) FROM videos),
  (SELECT count(*) FROM comments),
  (SELECT count(*) FROM comments WHERE quality_flags = '')
"""


def reproduce(
    conn: psycopg.Connection[Any],
    *,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
    topic_id: str = TOPIC_ID,
) -> dict[str, int]:
    """세 숫자를 `manifest.reproduces` 와 같은 이름으로 돌려준다."""
    with conn.cursor() as cur:
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if snapshot is None:
            raise LookupError("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        version = panel_version if panel_version is not None else panel.active_version(cur)
        if version is None:
            raise LookupError("no active panel roster (fork #31)")
        cur.execute(
            REPRODUCE_SQL,
            {
                "snapshot_id": snapshot,
                "panel_version": version,
                "topic_id": topic_id,
                "panel_role": PANEL_ROLE,
                "long_form": LONG_FORM,
            },
        )
        row = cur.fetchone()
    assert row is not None
    videos, comments, unique = row
    return {
        "선크림_장문_product": int(videos),
        "그_영상_댓글_전체": int(comments),
        "그_영상_댓글_중복제외": int(unique),
    }
