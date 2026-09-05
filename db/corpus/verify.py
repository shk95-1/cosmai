"""Recounts the manifest's three `reproduces` numbers on top of what was imported (fork #4, item 2).

These three numbers are the control the EPIC uses to ask "does cosmai produce the same numbers with the
same meaning". What happens here is **rewriting the same definition in SQL**, not making the numbers
match -- if they disagree, either the import is wrong or the contract's reading of it is wrong, and
neither is something this function should be fixing.

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

# The panel is always read through `panel_channel`'s active version -- a bare `WHERE active` quietly
# doubles the denominator if there are ever two active versions (fork #4's body, #31 review). The
# version number is chosen by `panel.active_version`.
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
    """Returns the three numbers under the same names as `manifest.reproduces`."""
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
