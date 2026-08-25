"""사전 적재의 한 자리. 시드(v1)와 `cosmai lexicon load --version n` 이 같은 SQL 로 쓴다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg
from psycopg.types.json import Json

ENTITY_COLUMNS = ("kind", "canonical", "surface", "tier", "source", "note")
ASPECT_COLUMNS = (
    "aspect",
    "scope",
    "category",
    "pattern",
    "is_neutral_noun",
    "ruleset",
    "priority",
    "extra",
)

ENTITY_SQL: LiteralString = """
INSERT INTO entity_lexicon (kind, canonical, surface, tier, source, note, version, active)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (kind, surface, version) DO NOTHING
"""
# ruleset/priority 는 002 로 왔고 v1 은 그전에 적재됐다. WHERE 가 이것을 1회 백필로 묶는다: 값이 한 번 들어간
# 행은 다시 쓰이지 않으므로 사전 내용은 여전히 버전으로만 바뀐다 (formats.md).
ASPECT_SQL: LiteralString = """
INSERT INTO aspect_lexicon
  (aspect, scope, category, pattern, is_neutral_noun, ruleset, priority, extra, version, active)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (aspect, scope, category, pattern, version) DO UPDATE
SET ruleset = EXCLUDED.ruleset, priority = EXCLUDED.priority
WHERE aspect_lexicon.ruleset = ''
"""
ENTITY_READ: LiteralString = """
SELECT surface, canonical || '|' || coalesce(tier, '') || '|' || coalesce(source, '')
FROM entity_lexicon WHERE kind = %s AND version = %s
"""
# UNIQUE 는 (aspect, scope, category, pattern, version) 이라 pattern 없이는 한 버전 안에서 키가 겹친다
# (v1 은 70행 → 55키). 구분자는 ' :: ': 카테고리 이름에 '/' 가 들어간다 ('헤어토닉/앰플').
ASPECT_READ: LiteralString = """
SELECT aspect || ' :: ' || scope || ' :: ' || category || ' :: ' || pattern,
       is_neutral_noun::text || ' | ' || priority::text || ' | ' || ruleset
         || ' | ' || extra::text
FROM aspect_lexicon WHERE version = %s
"""
ENTITY_ACTIVATE: LiteralString = "UPDATE entity_lexicon SET active = (version = %s) WHERE kind = %s"
ASPECT_ACTIVATE: LiteralString = "UPDATE aspect_lexicon SET active = (version = %s)"
ENTITY_ACTIVE: LiteralString = "SELECT max(version) FROM entity_lexicon WHERE kind = %s AND active"
ASPECT_ACTIVE: LiteralString = "SELECT max(version) FROM aspect_lexicon WHERE active"
ENTITY_COUNT: LiteralString = "SELECT count(*) FROM entity_lexicon WHERE kind = %s AND version = %s"
ASPECT_COUNT: LiteralString = "SELECT count(*) FROM aspect_lexicon WHERE version = %s"

ASPECT_KIND = "aspect"


@dataclass(frozen=True)
class Diff:
    kind: str
    version: int
    against: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


def _write(cur: psycopg.Cursor[Any], statement: LiteralString, rows: Sequence[Sequence[Any]]) -> int:
    if not rows:
        return 0
    cur.executemany(statement, rows)
    return max(cur.rowcount, 0)


def insert_entities(
    cur: psycopg.Cursor[Any], rows: Sequence[Sequence[Any]], version: int, active: bool = True
) -> int:
    """rows 는 ENTITY_COLUMNS 순서. 같은 (kind, surface, version) 재적재는 아무것도 바꾸지 않는다."""
    return _write(cur, ENTITY_SQL, [(*row, version, active) for row in rows])


def insert_aspects(
    cur: psycopg.Cursor[Any], rows: Sequence[Sequence[Any]], version: int, active: bool = True
) -> int:
    """rows 는 ASPECT_COLUMNS 순서. 마지막 칸(`extra`)은 psycopg 가 dict 를 jsonb 로 바꾸지 않으므로
    여기서 감싼다 -- 부르는 쪽이 감싸면 CSV 로더와 테스트가 각자 다른 모양을 넘긴다."""
    return _write(cur, ASPECT_SQL, [(*row[:-1], Json(row[-1] or {}), version, active) for row in rows])


def version_rows(cur: psycopg.Cursor[Any], kind: str, version: int) -> int:
    if kind == ASPECT_KIND:
        cur.execute(ASPECT_COUNT, (version,))
    else:
        cur.execute(ENTITY_COUNT, (kind, version))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def activate(cur: psycopg.Cursor[Any], kind: str, version: int) -> int:
    """빈 버전을 켜면 SET active = (version = n) 이 그 kind 를 통째로 끄고 아무 오류도 남기지 않는다."""
    if not version_rows(cur, kind, version):
        raise LookupError(f"{kind} has no rows at version {version}; nothing to activate")
    if kind == ASPECT_KIND:
        cur.execute(ASPECT_ACTIVATE, (version,))
    else:
        cur.execute(ENTITY_ACTIVATE, (version, kind))
    return max(cur.rowcount, 0)


def active_version(cur: psycopg.Cursor[Any], kind: str) -> int | None:
    if kind == ASPECT_KIND:
        cur.execute(ASPECT_ACTIVE)
    else:
        cur.execute(ENTITY_ACTIVE, (kind,))
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _read(cur: psycopg.Cursor[Any], kind: str, version: int) -> dict[str, str]:
    if kind == ASPECT_KIND:
        cur.execute(ASPECT_READ, (version,))
    else:
        cur.execute(ENTITY_READ, (kind, version))
    return {key: value for key, value in cur.fetchall()}


def diff(cur: psycopg.Cursor[Any], kind: str, version: int, against: int) -> Diff:
    new, old = _read(cur, kind, version), _read(cur, kind, against)
    return Diff(
        kind=kind,
        version=version,
        against=against,
        added=tuple(sorted(set(new) - set(old))),
        removed=tuple(sorted(set(old) - set(new))),
        changed=tuple(sorted(k for k in set(new) & set(old) if new[k] != old[k])),
    )
