"""Pre-approval 2 (issue #16): migrations after 001 may only add -- anything else needs a human.

The net is every directory under contracts/ddl/ except current/, which holds dump baselines rather
than migrations. It used to be contracts/ddl/needs alone, because that was the only migration
directory when the guard was written; contracts/ddl/tubedepth appeared later and sat outside it
until #111.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DDL_ROOT = Path(__file__).resolve().parents[1] / "contracts" / "ddl"
# Dumps of schemas other services own. They are the starting point the migrations layer onto, so
# they are not migrations and the "additive only" rule does not apply to them.
BASELINE_DIR = DDL_ROOT / "current"
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
# 키는 contracts/ddl/ 아래 상대 경로다 -- 파일명만으로는 디렉터리가 둘 이상인 지금 001_ 처럼 겹치는
# 이름에 남의 디렉터리 면제가 딸려 붙는다(#111).
SANCTIONED_DESTRUCTIVE = {
    # 사용자 승인 2026-08-24 — #5 운영 실패(btree 2704B 상한) + #12 안 A(키에 extractor_version).
    "needs/005_need_mention_natural_key.sql": ("DROP CONSTRAINT",),
}


def additive_dirs() -> list[Path]:
    return sorted(p for p in DDL_ROOT.iterdir() if p.is_dir() and p != BASELINE_DIR)


def creates_its_own_schema(directory: Path) -> bool:
    """001_ 은 그 디렉터리가 스키마를 **만드는** 경우에만 면제다 -- 그때 001 자체가 베이스라인이라
    비교할 이전 상태가 없다. current/ 에 덤프 베이스라인이 있는 스키마는 남이 이미 만든 것이므로
    그 001 도 남의 스키마에 얹는 추가분이고(contracts/ddl/tubedepth/001_comments_columns.sql),
    다른 파일과 똑같이 검사받아야 한다."""
    return not any(BASELINE_DIR.glob(f"*.{directory.name}.sql"))


def additive_files() -> list[Path]:
    files = []
    for directory in additive_dirs():
        exempt_001 = creates_its_own_schema(directory)
        files += [
            path
            for path in sorted(directory.glob("*.sql"))
            if not (exempt_001 and path.name.startswith("001_"))
        ]
    return files


def sanction_key(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def _statements(path: Path) -> str:
    return re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))


def destructive(path: Path) -> list[str]:
    return [re.sub(r"\s+", " ", m.group(0).upper()) for m in FORBIDDEN.finditer(_statements(path))]


def unsanctioned(path: Path) -> list[str]:
    """승인 목록과 정확히 같을 때만 빈 목록 — 목록에 없는 파일은 허용치가 () 이라 DROP 하나로 걸린다."""
    hits = destructive(path)
    return [] if hits == list(SANCTIONED_DESTRUCTIVE.get(sanction_key(path), ())) else hits


@pytest.mark.parametrize("path", additive_files(), ids=sanction_key)
def test_later_migrations_are_additive_only(path: Path):
    hits = unsanctioned(path)
    assert not hits, f"{path.name} is not additive-only (needs human approval): {hits}"


def test_every_sanctioned_exemption_still_names_a_file_that_exists():
    """승인은 그 파일 한 개에 붙은 것이다 — 파일이 사라지면 면제도 같이 사라져야 한다."""
    assert {name for name in SANCTIONED_DESTRUCTIVE if not (DDL_ROOT / name).exists()} == set()


def test_the_001_exemption_follows_who_created_the_schema_not_the_filename():
    """needs/001 은 스키마를 만드는 파일이라 면제, tubedepth/001 은 남의 스키마에 얹는 추가분이라
    검사 대상이다 -- 이름이 같아도 뜻이 다르다(#111)."""
    scanned = {sanction_key(p) for p in additive_files()}
    assert "needs/001_needs.sql" not in scanned
    assert "tubedepth/001_comments_columns.sql" in scanned


def test_an_unsanctioned_file_with_the_same_drop_still_fails(tmp_path: Path):
    (sanctioned,) = SANCTIONED_DESTRUCTIVE
    body = (DDL_ROOT / sanctioned).read_text(encoding="utf-8")
    copy = tmp_path / "006_someone_elses.sql"
    copy.write_text(body, encoding="utf-8")
    assert unsanctioned(copy) == ["DROP CONSTRAINT"]


def test_a_sanctioned_file_may_not_smuggle_a_second_destructive_statement(tmp_path: Path):
    (sanctioned,) = SANCTIONED_DESTRUCTIVE
    body = (DDL_ROOT / sanctioned).read_text(encoding="utf-8")
    # 면제는 <디렉터리>/<파일명> 에 붙으므로 사본도 같은 디렉터리 이름 아래 두어야 면제가 적용된다.
    smuggled = tmp_path / sanctioned
    smuggled.parent.mkdir(parents=True, exist_ok=True)
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
