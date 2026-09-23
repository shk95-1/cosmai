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

IDENT = r"[a-z_][a-z_0-9]*"
TABLE = rf"{IDENT}\.{IDENT}"
ALTER_DROP = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?(?P<table>{TABLE})\s+DROP\s+CONSTRAINT\s+"
    rf"(?P<name>{IDENT})\s*$",
    re.IGNORECASE | re.DOTALL,
)
ALTER_ADD = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?(?P<table>{TABLE})\s+ADD\s+CONSTRAINT\s+"
    rf"(?P<name>{IDENT})\s+(?P<body>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
CREATE_TABLE = re.compile(
    rf"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>{TABLE})\s*\(",
    re.IGNORECASE | re.DOTALL,
)
PLAIN_CHECK = re.compile(
    rf"^CHECK\s*\(\s*(?P<column>{IDENT})\s+IN\s*\((?P<values>[^()]*)\)\s*\)$",
    re.IGNORECASE | re.DOTALL,
)
STRING_LIST = re.compile(r"\s*'(?:''|[^'])*'(?:\s*,\s*'(?:''|[^'])*')*\s*", re.DOTALL)
STRING_VALUE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
INLINE_COLUMN = re.compile(rf"^\s*(?P<column>{IDENT})\s+[^\n]*\bCHECK\b", re.IGNORECASE)
NAMED_CHECK = re.compile(rf"^\s*CONSTRAINT\s+(?P<name>{IDENT})\s+(?P<body>.+)$", re.IGNORECASE)


def _plain_check(body: str) -> tuple[str, frozenset[str]] | None:
    match = PLAIN_CHECK.fullmatch(body.strip())
    if match is None or STRING_LIST.fullmatch(match["values"]) is None:
        return None
    values = frozenset(
        item.group(0)[1:-1].replace("''", "'") for item in STRING_VALUE.finditer(match["values"])
    )
    return match["column"].lower(), values


def _sql_parts(path: Path) -> list[str]:
    # The guard only accepts a plain ALTER TABLE statement. Semicolons inside an unrelated function
    # may split that function, but can never make an unsupported DROP fit the anchored pattern below.
    return [part.strip() for part in _statements(path).split(";") if part.strip()]


def _check_on_create_line(line: str) -> tuple[str, frozenset[str]] | None:
    body = line[line.upper().index("CHECK") :].strip()
    for _ in range(3):
        parsed = _plain_check(body)
        if parsed is not None:
            return parsed
        if not body.endswith((",", ")")):
            break
        body = body[:-1].rstrip()
    return None


def _old_check(path: Path, table: str, name: str) -> tuple[str, frozenset[str]] | None:
    schema = path.parent.name
    baseline = DDL_ROOT / "current" / f"app.{schema}.sql"
    earlier = sorted(p for p in (DDL_ROOT / schema).glob("*.sql") if p.name < path.name)
    files = [*([baseline] if baseline.exists() else []), *earlier]
    found: tuple[str, frozenset[str]] | None = None
    for prior in files:
        for part in _sql_parts(prior):
            drop = ALTER_DROP.fullmatch(part)
            if drop and (drop["table"].lower(), drop["name"].lower()) == (table, name):
                found = None
            add = ALTER_ADD.fullmatch(part)
            if add and (add["table"].lower(), add["name"].lower()) == (table, name):
                found = _plain_check(add["body"])
            create = CREATE_TABLE.match(part)
            if create is None or create["table"].lower() != table:
                continue
            short_table = table.split(".", 1)[1]
            for line in part[create.end() :].splitlines():
                inline = INLINE_COLUMN.match(line)
                if inline and f"{short_table}_{inline['column'].lower()}_check" == name:
                    found = _check_on_create_line(line)
                named = NAMED_CHECK.match(line)
                if named and named["name"].lower() == name:
                    found = _check_on_create_line(named["body"])
    return found


def _check_widening_reason(path: Path, drop_part: str, parts: list[str]) -> str | None:
    drop = ALTER_DROP.fullmatch(drop_part)
    if drop is None:
        return "unsupported DROP CONSTRAINT syntax"
    table, name = drop["table"].lower(), drop["name"].lower()
    later = parts[parts.index(drop_part) + 1 :]
    replacements = [
        add
        for part in later
        if (add := ALTER_ADD.fullmatch(part)) and (add["table"].lower(), add["name"].lower()) == (table, name)
    ]
    if len(replacements) != 1:
        return "same-named replacement on the same table is missing or repeated"
    new = _plain_check(replacements[0]["body"])
    if new is None:
        return "replacement is not a plain IN-list CHECK"
    old = _old_check(path, table, name)
    if old is None:
        return "old definition is not a plain IN-list CHECK"
    if old[0] != new[0]:
        return "replacement changes the checked column"
    if not old[1] <= new[1]:
        return "replacement removes permitted values"
    return None


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
    if hits == list(SANCTIONED_DESTRUCTIVE.get(sanction_key(path), ())):
        return []
    if not path.is_relative_to(DDL_ROOT) or not hits or any(hit != "DROP CONSTRAINT" for hit in hits):
        return hits
    parts = _sql_parts(path)
    drops = [part for part in parts if re.search(r"\bDROP\s+CONSTRAINT\b", part, re.IGNORECASE)]
    if len(drops) != len(hits):
        return hits
    for part in drops:
        reason = _check_widening_reason(path, part, parts)
        if reason is not None:
            return [f"DROP CONSTRAINT: {reason}"]
    return []


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


# Per entry since the list grew past one (#285): unpacking a single exemption and checking that one
# would leave every exemption added after it checked by nothing at all.
@pytest.mark.parametrize("sanctioned", sorted(SANCTIONED_DESTRUCTIVE))
def test_an_unsanctioned_file_with_the_same_drop_still_fails(sanctioned: str, tmp_path: Path):
    body = (DDL_ROOT / sanctioned).read_text(encoding="utf-8")
    copy = tmp_path / "006_someone_elses.sql"
    copy.write_text(body, encoding="utf-8")
    assert unsanctioned(copy) == list(SANCTIONED_DESTRUCTIVE[sanctioned])


@pytest.mark.parametrize("sanctioned", sorted(SANCTIONED_DESTRUCTIVE))
def test_a_sanctioned_file_may_not_smuggle_a_second_destructive_statement(sanctioned: str, tmp_path: Path):
    body = (DDL_ROOT / sanctioned).read_text(encoding="utf-8")
    # 면제는 <디렉터리>/<파일명> 에 붙으므로 사본도 같은 디렉터리 이름 아래 두어야 면제가 적용된다.
    smuggled = tmp_path / sanctioned
    smuggled.parent.mkdir(parents=True, exist_ok=True)
    smuggled.write_text(body + "\nALTER TABLE needs.need_mention DROP COLUMN marker;\n", encoding="utf-8")
    assert unsanctioned(smuggled) == [*SANCTIONED_DESTRUCTIVE[sanctioned], "DROP COLUMN"]


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


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        ("CHECK (dataset IN ('a', 'b', 'c'))", None),
        ("CHECK (dataset IN ('a'))", "removes permitted values"),
        ("CHECK (dataset <> '')", "plain IN-list"),
        (None, "same-named replacement"),
    ],
)
def test_check_widening_class_through_the_real_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str | None, reason: str | None
):
    root = tmp_path / "ddl"
    needs = root / "needs"
    needs.mkdir(parents=True)
    (needs / "004_old.sql").write_text(
        "CREATE TABLE needs.sample (dataset text CHECK (dataset IN ('a', 'b')));\n",
        encoding="utf-8",
    )
    path = needs / "012_change.sql"
    body = "ALTER TABLE needs.sample DROP CONSTRAINT sample_dataset_check;\n"
    if replacement is not None:
        body += f"ALTER TABLE needs.sample ADD CONSTRAINT sample_dataset_check {replacement};\n"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setitem(globals(), "DDL_ROOT", root)
    if reason is None:
        test_later_migrations_are_additive_only(path)
    else:
        with pytest.raises(AssertionError, match=f"012_change.sql.*{reason}"):
            test_later_migrations_are_additive_only(path)


def test_check_widening_rejects_a_renamed_constraint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "ddl"
    needs = root / "needs"
    needs.mkdir(parents=True)
    (needs / "004_old.sql").write_text(
        "CREATE TABLE needs.sample (dataset text CHECK (dataset IN ('a', 'b')));\n",
        encoding="utf-8",
    )
    path = needs / "012_change.sql"
    path.write_text(
        "ALTER TABLE needs.sample DROP CONSTRAINT sample_dataset_check;\n"
        "ALTER TABLE needs.sample ADD CONSTRAINT sample_dataset_check_new "
        "CHECK (dataset IN ('a', 'b', 'c'));\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "DDL_ROOT", root)
    with pytest.raises(AssertionError, match="012_change.sql.*same-named replacement"):
        test_later_migrations_are_additive_only(path)


def test_check_widening_finds_a_named_old_check_in_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "ddl"
    current = root / "current"
    current.mkdir(parents=True)
    (current / "app.tubedepth.sql").write_text(
        "ALTER TABLE ONLY tubedepth.sample ADD CONSTRAINT sample_status_check "
        "CHECK (status IN ('a', 'b'));\n",
        encoding="utf-8",
    )
    new = root / "tubedepth"
    new.mkdir()
    path = new / "012_change.sql"
    path.write_text(
        "ALTER TABLE tubedepth.sample DROP CONSTRAINT sample_status_check;\n"
        "ALTER TABLE tubedepth.sample ADD CONSTRAINT sample_status_check "
        "CHECK (status IN ('a', 'b', 'c'));\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "DDL_ROOT", root)
    test_later_migrations_are_additive_only(path)
