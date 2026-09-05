"""The one place lexicon loading happens. The seed (v1) and `cosmai lexicon load --version n` write
through the same SQL."""

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
# ruleset/priority arrived with 002, and v1 was loaded before that. The WHERE ties this to a one-time
# backfill: a row that already has a value never gets rewritten, so the dictionary's content still only
# changes by version (formats.md).
ASPECT_SQL: LiteralString = """
INSERT INTO aspect_lexicon
  (aspect, scope, category, pattern, is_neutral_noun, ruleset, priority, extra, version, active)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (aspect, scope, category, pattern, version) DO UPDATE
SET ruleset = EXCLUDED.ruleset, priority = EXCLUDED.priority
WHERE aspect_lexicon.ruleset = ''
"""
# There is **only one** expression that builds a key and a value. Re-rendering the source CSV side in
# Python would turn every row into a "changed" one over nothing more than jsonb's key order, so the CSV
# is also pushed through DB VALUES and read back so it runs the same expression (fork #62).
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
# One aspect version holds several rulesets (formats.md B4). A CSV is the loading source for just one of
# them, so without narrowing, every other ruleset comes back as "removed" and buries the very dictionary
# this was meant to compare against.
ASPECT_READ_RULESETS: LiteralString = f"{ASPECT_READ} AND ruleset = ANY(%s)"
# VALUES infers column types from the first row -- attaching a cast keeps the expression above running
# on the same type as the DB column no matter what any given adapter sent.
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
    # Both sides are **labels** -- one side can be the loading source CSV rather than a DB version
    # (fork #62).
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
    """rows follows ENTITY_COLUMNS order. Re-loading the same (kind, surface, version) changes
    nothing."""
    return _write(cur, ENTITY_SQL, [(*row, version, active) for row in rows])


def insert_aspects(
    cur: psycopg.Cursor[Any], rows: Sequence[Sequence[Any]], version: int, active: bool = True
) -> int:
    """rows follows ASPECT_COLUMNS order. The last field (`extra`) is wrapped here because psycopg does
    not turn a dict into jsonb on its own -- if the caller had to wrap it instead, the CSV loader and
    the tests would each pass a different shape."""
    return _write(cur, ASPECT_SQL, [(*row[:-1], Json(row[-1] or {}), version, active) for row in rows])


def version_rows(cur: psycopg.Cursor[Any], kind: str, version: int) -> int:
    if kind == ASPECT_KIND:
        cur.execute(ASPECT_COUNT, (version,))
    else:
        cur.execute(ENTITY_COUNT, (kind, version))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def activate(cur: psycopg.Cursor[Any], kind: str, version: int) -> int:
    """Activating an empty version has SET active = (version = n) turn that whole kind off, with no
    error raised."""
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
    """Renders CSV rows through **the same expression as the DB**. An empty CSV is an empty dict, not
    SQL -- `VALUES` with no rows is a syntax error, and that error says something worse than "the CSV
    was empty"."""
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
    """The rulesets a CSV names. If it is not aspect, there is no axis to narrow by, so this is
    empty."""
    if kind != ASPECT_KIND:
        return []
    at = ASPECT_COLUMNS.index("ruleset")
    return sorted({str(row[at]) for row in rows})


def diff_csv(cur: psycopg.Cursor[Any], kind: str, rows: Sequence[Sequence[Any]], against: int) -> Diff:
    """Compares one loading-source CSV against a DB version (fork #62). `rows` is the same order
    `insert_*` takes -- loading and comparing must go through the same transform for the answer to "is
    this CSV that version" to agree with what loading itself would answer."""
    rulesets = csv_rulesets(kind, rows)
    label = f"v{against}" + (f" (ruleset={','.join(rulesets)})" if rulesets else "")
    return _compare(kind, "csv", label, _read_csv(cur, kind, rows), _read(cur, kind, against, rulesets))
