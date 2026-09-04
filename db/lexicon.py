"""사전 적재의 한 자리. 시드(v1)와 `cosmai lexicon load --version n` 이 같은 SQL 로 쓴다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg
from psycopg import sql
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
# 키와 값을 만드는 식은 **한 벌뿐이다**. 적재 원본 CSV 쪽을 파이썬으로 다시 렌더하면 jsonb 의 키 순서
# 하나로 전 행이 "바뀜"이 되므로, CSV 도 같은 식을 태우려고 VALUES 로 DB 에 넣어 읽는다 (포크 #62).
ENTITY_KEY: LiteralString = "surface"
ENTITY_VALUE: LiteralString = "canonical || '|' || coalesce(tier, '') || '|' || coalesce(source, '')"
# UNIQUE 는 (aspect, scope, category, pattern, version) 이라 pattern 없이는 한 버전 안에서 키가 겹친다
# (v1 은 70행 → 55키). 구분자는 ' :: ': 카테고리 이름에 '/' 가 들어간다 ('헤어토닉/앰플').
ASPECT_KEY: LiteralString = "aspect || ' :: ' || scope || ' :: ' || category || ' :: ' || pattern"
ASPECT_VALUE: LiteralString = (
    "is_neutral_noun::text || ' | ' || priority::text || ' | ' || ruleset || ' | ' || extra::text"
)
ENTITY_READ: LiteralString = f"""
SELECT {ENTITY_KEY}, {ENTITY_VALUE} FROM entity_lexicon WHERE kind = %s AND version = %s
"""
ASPECT_READ: LiteralString = f"""
SELECT {ASPECT_KEY}, {ASPECT_VALUE} FROM aspect_lexicon WHERE version = %s
"""
# 한 aspect 버전에는 룰셋이 여럿 산다(formats.md B4). CSV 는 그중 하나의 적재 원본이라, 안 좁히면
# 다른 룰셋 전부가 "지워짐"으로 나와 정작 맞대려던 사전이 그 목록에 묻힌다.
ASPECT_READ_RULESETS: LiteralString = f"{ASPECT_READ} AND ruleset = ANY(%s)"
# VALUES 는 열 타입을 첫 행에서 추론한다 -- 캐스트를 붙여 두면 어느 어댑터가 무엇을 보냈든 위 식이
# DB 열과 같은 타입 위에서 돈다.
ENTITY_VALUES: LiteralString = """
SELECT v.column2::text AS canonical, v.column3::text AS surface,
       v.column4::text AS tier, v.column5::text AS source
FROM (VALUES {rows}) AS v
"""
ASPECT_VALUES: LiteralString = """
SELECT v.column1::text AS aspect, v.column2::text AS scope, v.column3::text AS category,
       v.column4::text AS pattern, v.column5::boolean AS is_neutral_noun,
       v.column6::text AS ruleset, v.column7::int AS priority, v.column8::jsonb AS extra
FROM (VALUES {rows}) AS v
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
    # 양쪽은 **이름표**다 -- 한쪽이 DB 버전이 아니라 적재 원본 CSV 일 수 있다 (포크 #62).
    version: str
    against: str
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


def _read(
    cur: psycopg.Cursor[Any], kind: str, version: int, rulesets: Sequence[str] | None = None
) -> dict[str, str]:
    if kind != ASPECT_KIND:
        cur.execute(ENTITY_READ, (kind, version))
    elif rulesets:
        cur.execute(ASPECT_READ_RULESETS, (version, list(rulesets)))
    else:
        cur.execute(ASPECT_READ, (version,))
    return {key: value for key, value in cur.fetchall()}


def _read_csv(cur: psycopg.Cursor[Any], kind: str, rows: Sequence[Sequence[Any]]) -> dict[str, str]:
    """CSV 행들을 **DB 와 같은 식**으로 렌더한다. 빈 CSV 는 SQL 이 아니라 빈 사전이다 --
    `VALUES` 에 행이 없으면 문법 오류이고, 그 오류는 "CSV 가 비었다"보다 나쁜 말이다."""
    if not rows:
        return {}
    if kind == ASPECT_KIND:
        wide, key, value = ASPECT_VALUES, ASPECT_KEY, ASPECT_VALUE
        sent = [(*row[:-1], Json(row[-1] or {})) for row in rows]
    else:
        wide, key, value = ENTITY_VALUES, ENTITY_KEY, ENTITY_VALUE
        sent = [tuple(row) for row in rows]
    one = sql.SQL("({})").format(sql.SQL(", ").join([sql.Placeholder()] * len(sent[0])))
    cur.execute(
        sql.SQL("SELECT {key}, {value} FROM ({wide}) AS csv").format(
            key=sql.SQL(key),
            value=sql.SQL(value),
            wide=sql.SQL(wide).format(rows=sql.SQL(", ").join([one] * len(sent))),
        ),
        [field for row in sent for field in row],
    )
    return {key_: value_ for key_, value_ in cur.fetchall()}


def _compare(kind: str, version: str, against: str, new: dict[str, str], old: dict[str, str]) -> Diff:
    return Diff(
        kind=kind,
        version=version,
        against=against,
        added=tuple(sorted(set(new) - set(old))),
        removed=tuple(sorted(set(old) - set(new))),
        changed=tuple(sorted(k for k in set(new) & set(old) if new[k] != old[k])),
    )


def diff(cur: psycopg.Cursor[Any], kind: str, version: int, against: int) -> Diff:
    return _compare(kind, f"v{version}", f"v{against}", _read(cur, kind, version), _read(cur, kind, against))


def csv_rulesets(kind: str, rows: Sequence[Sequence[Any]]) -> list[str]:
    """CSV 가 말하는 룰셋. aspect 가 아니면 좁힐 축이 없어 빈 목록이다."""
    if kind != ASPECT_KIND:
        return []
    at = ASPECT_COLUMNS.index("ruleset")
    return sorted({str(row[at]) for row in rows})


def diff_csv(cur: psycopg.Cursor[Any], kind: str, rows: Sequence[Sequence[Any]], against: int) -> Diff:
    """적재 원본 CSV 한 벌을 DB 의 한 버전과 맞댄다 (포크 #62). `rows` 는 `insert_*` 가 받는 그 순서다 --
    적재와 대조가 같은 변환을 타야 "이 CSV 가 그 버전인가"의 답이 적재의 답과 같다."""
    rulesets = csv_rulesets(kind, rows)
    label = f"v{against}" + (f" (ruleset={','.join(rulesets)})" if rulesets else "")
    return _compare(kind, "csv", label, _read_csv(cur, kind, rows), _read(cur, kind, against, rulesets))
