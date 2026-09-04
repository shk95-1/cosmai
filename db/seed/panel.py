"""패널 명부 43채널 → `needs.panel_roster` + `needs.panel_channel` (포크 #31).

ydc 의 모든 비율이 이 명부를 분모로 쓴다. 원본은 `eval/panel/channels_v1.csv` 11열이고 여섯 열만
표로 간다 -- 무엇을 왜 버리는지는 `contracts/formats.md` §패널 명부 CSV 가 진다.

판본은 사전(`entity_lexicon`·`aspect_lexicon`)과 같은 모양이다: 적재 때 `version` 을 부여하고
`active` 로 교체한다 (contracts/versioning.md). 다만 사전과 달리 이 표는 **분모**라, 활성 판본이
둘이면 `(version, panel_role) WHERE active` 부분 인덱스를 타는 조회가 43 대신 86 을 센다. 그 불변식은
DDL 이 아니라 이 파일이 진다(#3 리뷰 L6): "활성 행의 distinct version 이 하나"는 부분 유니크 인덱스로
쓸 수 없는 문장이고, 한 문장짜리 `SET active = (version = %s)` 는 그것을 원자적으로 만든다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.seed._common import counts, opt, read_csv

TABLES = ("panel_roster", "panel_channel")

PANEL_VERSION = 1
PANEL_NOTE = "seed:channels_v1"
PANEL_CSV = Path("panel") / "channels_v1.csv"
# rows() 가 내놓는 순서. CHANNEL_SQL 의 자리표와 같아야 한다.
COLUMNS = ("channel_id", "panel_role", "handle", "channel_title", "role_basis", "source_list")

ROSTER_SQL: LiteralString = """
INSERT INTO panel_roster (version, note) VALUES (%s, %s)
ON CONFLICT (version) DO NOTHING
"""
# 사전과 같은 DO NOTHING: 명부 내용은 재적재가 아니라 다음 판본으로 바뀐다 (formats.md).
CHANNEL_SQL: LiteralString = """
INSERT INTO panel_channel
  (version, channel_id, panel_role, handle, channel_title, role_basis, source_list)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (version, channel_id) DO NOTHING
"""
# WHERE 가 있어야 재실행이 값이 같은 행을 다시 쓰지 않는다 -- "변경 0" 이 rowcount 로 읽힌다.
ACTIVATE_SQL: LiteralString = """
UPDATE panel_channel SET active = (version = %s)
WHERE active IS DISTINCT FROM (version = %s)
"""
VERSION_COUNT_SQL: LiteralString = "SELECT count(*) FROM panel_channel WHERE version = %s"
ACTIVE_SQL: LiteralString = "SELECT DISTINCT version FROM panel_channel WHERE active"


def rows(source_dir: Path) -> list[tuple[Any, ...]]:
    """CSV 한 줄 → COLUMNS 순서의 한 행. 나머지 다섯 열은 여기서 떨어진다."""
    return [
        (
            r["channel_id"],
            r["panel_role"],
            opt(r["handle"]),
            r["channel_title"],
            r["role_basis"],
            r["source_list"],
        )
        for r in read_csv(source_dir / PANEL_CSV)
    ]


def insert(cur: psycopg.Cursor[Any], panel_rows: Sequence[Sequence[Any]], version: int, note: str) -> int:
    """판본 한 줄과 그 명부를 넣는다. 명부 행이 FK 로 가리킬 부모가 먼저 서야 한다."""
    cur.execute(ROSTER_SQL, (version, note))
    if not panel_rows:
        return 0
    cur.executemany(CHANNEL_SQL, [(version, *row) for row in panel_rows])
    return max(cur.rowcount, 0)


def activate(cur: psycopg.Cursor[Any], version: int) -> int:
    """이 판본만 켠다. 빈 판본을 켜면 `SET active = (version = n)` 이 명부를 통째로 끄고, 분모가 0 이
    된 조회는 오류 없이 비율을 못 낸다 -- 그래서 행이 없으면 거절한다 (db/lexicon.activate 와 같다)."""
    cur.execute(VERSION_COUNT_SQL, (version,))
    row = cur.fetchone()
    if not (row and row[0]):
        raise LookupError(f"panel_channel has no rows at version {version}; nothing to activate")
    cur.execute(ACTIVATE_SQL, (version, version))
    return max(cur.rowcount, 0)


def active_version(cur: psycopg.Cursor[Any]) -> int | None:
    """활성 판본. 둘이면 답을 고르지 않고 멈춘다 -- 고르면 그 자리는 분모를 두 번 센 43+43 을 낸다."""
    cur.execute(ACTIVE_SQL)
    versions = sorted(int(v) for (v,) in cur.fetchall())
    if len(versions) > 1:
        raise ValueError(f"panel_channel has {len(versions)} active versions: {versions}")
    return versions[0] if versions else None


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    insert(cur, rows(source_dir), PANEL_VERSION, PANEL_NOTE)
    activate(cur, PANEL_VERSION)
    return counts(cur, TABLES)
