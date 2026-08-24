"""Pre-approval 2 (issue #16): migrations after 001 may only add -- anything else needs a human."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DDL_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs"
# 이 정규식이 사전 승인 2("추가만")의 유일한 기계 검사다: 어휘가 좁으면 승인 밖 변경이 그대로 통과한다.
FORBIDDEN = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN|SCHEMA|INDEX|CONSTRAINT|VIEW|FUNCTION|PROCEDURE|TRIGGER|TYPE|SEQUENCE"
    r"|DOMAIN|OWNED|DATABASE|ROLE|DEFAULT|NOT\s+NULL)"
    r"|ALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE"
    r"|TRUNCATE|DELETE\s+FROM|UPDATE\s+\S+\s+SET|MERGE\s+INTO|REVOKE"
    r"|RENAME\s+(TO|COLUMN)|SET\s+NOT\s+NULL)\b",
    re.IGNORECASE,
)
# 사람이 승인한 파괴적 마이그레이션. 값은 그 파일이 해도 되는 파괴적 문장 전부다 — 승인 밖 문장을
# 하나 더 끼워넣으면 목록이 어긋나 실패한다.
SANCTIONED_DESTRUCTIVE = {
    # 사용자 승인 2026-08-24 — #5 운영 실패(btree 2704B 상한) + #12 안 A(키에 extractor_version).
    "005_need_mention_natural_key.sql": ("DROP CONSTRAINT",),
}


def _statements(path: Path) -> str:
    return re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))


def destructive(path: Path) -> list[str]:
    return [re.sub(r"\s+", " ", m.group(0).upper()) for m in FORBIDDEN.finditer(_statements(path))]


def unsanctioned(path: Path) -> list[str]:
    """승인 목록과 정확히 같을 때만 빈 목록 — 목록에 없는 파일은 허용치가 () 이라 DROP 하나로 걸린다."""
    hits = destructive(path)
    return [] if hits == list(SANCTIONED_DESTRUCTIVE.get(path.name, ())) else hits


@pytest.mark.parametrize("path", sorted(p for p in DDL_DIR.glob("*.sql") if not p.name.startswith("001_")))
def test_later_migrations_are_additive_only(path: Path):
    hits = unsanctioned(path)
    assert not hits, f"{path.name} is not additive-only (needs human approval): {hits}"


def test_every_sanctioned_exemption_still_names_a_file_that_exists():
    """승인은 그 파일 한 개에 붙은 것이다 — 파일이 사라지면 면제도 같이 사라져야 한다."""
    assert {name for name in SANCTIONED_DESTRUCTIVE if not (DDL_DIR / name).exists()} == set()


def test_an_unsanctioned_file_with_the_same_drop_still_fails(tmp_path: Path):
    (sanctioned,) = SANCTIONED_DESTRUCTIVE
    body = (DDL_DIR / sanctioned).read_text(encoding="utf-8")
    copy = tmp_path / "006_someone_elses.sql"
    copy.write_text(body, encoding="utf-8")
    assert unsanctioned(copy) == ["DROP CONSTRAINT"]


def test_a_sanctioned_file_may_not_smuggle_a_second_destructive_statement(tmp_path: Path):
    (sanctioned,) = SANCTIONED_DESTRUCTIVE
    body = (DDL_DIR / sanctioned).read_text(encoding="utf-8")
    smuggled = tmp_path / sanctioned
    smuggled.write_text(body + "\nALTER TABLE needs.need_mention DROP COLUMN marker;\n", encoding="utf-8")
    assert unsanctioned(smuggled) == ["DROP CONSTRAINT", "DROP COLUMN"]


def test_the_guard_catches_a_drop(tmp_path: Path):
    bad = tmp_path / "002_x.sql"
    bad.write_text(
        "ALTER TABLE needs.x DROP COLUMN y; -- DROP TABLE in a comment is fine\n", encoding="utf-8"
    )
    assert unsanctioned(bad)
    ok = tmp_path / "003_x.sql"
    ok.write_text("ALTER TABLE needs.x ADD COLUMN z int;\nCREATE INDEX ON needs.x (z);\n", encoding="utf-8")
    assert not unsanctioned(ok)


@pytest.mark.parametrize(
    "statement",
    [
        "DROP VIEW needs.v;",
        "DROP FUNCTION needs.f();",
        "DROP TRIGGER t ON needs.x;",
        "DROP SEQUENCE needs.s;",
        "DROP OWNED BY needs_runtime;",
        "ALTER TABLE needs.x ALTER COLUMN y DROP DEFAULT;",
        "ALTER TABLE needs.x ALTER COLUMN y DROP NOT NULL;",
        "REVOKE SELECT ON needs.x FROM needs_runtime;",
        "MERGE INTO needs.x USING needs.y ON true;",
    ],
)
def test_the_guard_catches_the_other_ways_to_take_something_away(tmp_path: Path, statement: str):
    path = tmp_path / "004_x.sql"
    path.write_text(statement + "\n", encoding="utf-8")
    assert unsanctioned(path), statement
