"""contracts/ddl/needs/foreign.txt: 운영 needs 에 들어온 남의 DDL 을 선언하는 목록 (#75).

tool/checks/ddl-drift 만 이 파일을 읽지만, 그 검사는 운영 DB 와 docker 를 요구해서 스위트에서
돌 수 없다. 여기서 도는 것은 파일이 지키는 약속 쪽이다 — 형식, 그리고 "제외되는 것 중에 우리
것은 없다"(우리 것을 제외하면 그 객체의 드리프트가 영원히 안 보인다).

그 약속의 문장이 upstream 과 포크에서 달라진다(upstream cosmai#108). 같은 목록이 upstream 에서는
전부 '남의 것' 이고 포크 cosmai-import-ydc 에서는 020~025 와 db/views/ 가 그 아홉을 스스로
선언해 전부 '우리 것' 이다. 그래서 판정을 목록이 아니라 **체크아웃**에 묻는다: 이 체크아웃이
선언하는 객체는 목록에서 무시하고(= 제외하지 않고 그대로 대조한다), 나머지만 제외한다.
무시는 검사를 약하게 만들지 않는다 — 무시된 객체는 기대치에도 서고 운영 덤프에도 남아 여전히
맞대어진다. 그 규칙을 구현하는 것은 tool/ddl-foreign-entries 하나이고, 아래 테스트는 선언 목록을
주입해 규칙이 켜지고 꺼지는 것을 되묻는다.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = REPO_ROOT / "contracts" / "ddl" / "needs"
VIEW_DIR = REPO_ROOT / "db" / "views"
FOREIGN = DDL_DIR / "foreign.txt"
DRIFT = REPO_ROOT / "tool" / "checks" / "ddl-drift"
FILTER = REPO_ROOT / "tool" / "ddl-foreign-entries"
ENTRY = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


def entries() -> list[str]:
    """ddl-drift 의 `sed 's/#.*//' | awk 'NF {print $1}'` 과 같은 읽기."""
    out = []
    for line in FOREIGN.read_text(encoding="utf-8").splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            out.append(fields[0])
    return out


def declared_tables() -> set[str]:
    tables: set[str] = set()
    for path in sorted(DDL_DIR.glob("*.sql")):
        tables |= set(re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8")))
    return tables


def declared_views() -> set[str]:
    """뷰도 이 체크아웃의 선언이다 — db/migrate.sh 가 배포마다 db/views/*.sql 을 다시 세우고,
    pg_dump 의 -T 는 뷰에도 걸려서 목록에 오르면 테이블과 똑같이 제외된다."""
    views: set[str] = set()
    for path in sorted(VIEW_DIR.glob("*.sql")):
        views |= set(re.findall(r"CREATE VIEW needs\.(\w+)", path.read_text(encoding="utf-8")))
    return views


def declared_columns() -> dict[str, set[str]]:
    """CREATE TABLE 본문의 컬럼 + 나중 마이그레이션의 ADD COLUMN. PRIMARY KEY·UNIQUE 같은 제약 줄은
    대문자로 시작해서, 소문자 식별자만 받는 아래 정규식에 애초에 안 걸린다."""
    columns: dict[str, set[str]] = {}
    for path in sorted(DDL_DIR.glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        for table, column in re.findall(r"ALTER TABLE needs\.(\w+)\s+ADD COLUMN (\w+)", body):
            columns.setdefault(table, set()).add(column)
        for table, inner in re.findall(r"CREATE TABLE needs\.(\w+)\s*\((.*?)\n\);", body, re.S):
            for line in inner.splitlines():
                name = re.match(r"([a-z_][a-z0-9_]*)\s", re.sub(r"--.*", "", line).strip())
                if name:
                    columns.setdefault(table, set()).add(name.group(1))
    return columns


def declared_objects() -> set[str]:
    """ddl-drift 가 일회용 컨테이너의 pg_catalog 에 묻는 것과 같은 목록을, 파일에서 읽어 만든다.
    두 읽기가 갈리면 아래 test_the_survivors_are_exactly_what_this_checkout_does_not_declare 가 운다."""
    objects = declared_tables() | declared_views()
    for table, columns in declared_columns().items():
        objects |= {f"{table}.{column}" for column in columns}
    return objects


def kinds(declared: Iterable[str], tmp: Path) -> list[str]:
    """주입한 선언 목록으로 tool/ddl-foreign-entries 를 돌려, ddl-drift 가 받는 줄을 그대로 돌려준다."""
    listing = tmp / "declared.txt"
    listing.write_text("".join(f"{name}\n" for name in sorted(declared)), encoding="utf-8")
    out = subprocess.run(
        [str(FILTER), str(FOREIGN), str(listing)], capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def survivors(declared: Iterable[str], tmp: Path) -> list[str]:
    """제외될 항목의 이름만 — 무시된 것은 여기 없다."""
    return [line.split(" ", 1)[1] for line in kinds(declared, tmp)]


def test_the_file_states_the_convention_it_exists_for():
    body = FOREIGN.read_text(encoding="utf-8")
    assert "남의 DDL 이 운영에 들어오면 선언한다" in body
    # 같은 목록이 두 레포에서 뜻이 달라지는 이유를 파일 머리가 지고 있어야 한다 (cosmai#108).
    assert "이 체크아웃이 선언하는 객체는 무시한다" in body


def test_every_entry_is_a_table_or_a_table_column():
    assert entries(), "빈 목록은 이 파일이 필요 없다는 뜻이다 — 지워라"
    assert [e for e in entries() if not ENTRY.match(e)] == []


def test_the_first_entry_is_the_forks_retrieval_chunk():
    assert entries()[0] == "retrieval_chunk"


def test_nothing_this_checkout_declares_is_ever_excluded(tmp_path: Path):
    """원래 가드가 지키던 값 그대로다 — 우리 객체가 운영 덤프에서 빠지면 그 드리프트가 영원히 안
    보인다. 두 레포에서 답이 다르고 문장은 같다: upstream 은 아홉을 하나도 선언하지 않아 아홉이
    다 제외되고, 포크는 아홉을 다 선언해 하나도 제외되지 않는다."""
    assert set(survivors(declared_objects(), tmp=tmp_path)) & declared_objects() == set()


def test_the_survivors_are_exactly_what_this_checkout_does_not_declare(tmp_path: Path):
    """sh 쪽 규칙과 위 파이썬 읽기가 같은 답을 내는지 — 주석 떼기·테이블/컬럼 가르기가 갈리면 운다."""
    declared = declared_objects()
    assert survivors(declared, tmp=tmp_path) == [e for e in entries() if e not in declared]


def upstream_shaped() -> set[str]:
    """upstream 체크아웃의 선언 집합 모양 — 001~008 은 선언하고 이 아홉은 하나도 선언하지 않는다.
    `aspect_lexicon` 은 남고 `aspect_lexicon.extra` 만 빠지는 것까지 그쪽과 같다(021 이 포크 것이다)."""
    listed = set(entries())
    return {o for o in declared_objects() if o not in listed and o.split(".")[0] not in listed}


def test_an_upstream_checkout_declares_none_of_them_so_the_rule_is_off(tmp_path: Path):
    """규칙이 켜지는 조건은 '목록이 비었다' 가 아니라 '이 체크아웃이 그것을 선언한다' 이다.
    upstream 모양의 선언 집합(비어 있지 않다)을 주면 아홉이 전부 살아남고, ddl-drift 가 만드는
    제외 목록은 이 규칙이 생기기 전과 항목도 순서도 같다."""
    declared = upstream_shaped()
    assert declared, "빈 목록으로 upstream 을 흉내 내면 아래 단언이 다른 이유로 통과한다"
    assert "aspect_lexicon" in declared and "aspect_lexicon.extra" not in declared
    assert survivors(declared, tmp=tmp_path) == entries()
    assert kinds(declared, tmp=tmp_path)[:2] == [
        "table retrieval_chunk",
        "column aspect_lexicon.extra",
    ]


def test_an_empty_declaration_list_stops_the_check_instead_of_quietly_widening_it(tmp_path: Path):
    """이 규칙이 조용히 실패하는 방향은 하나다: 선언 목록이 비면 아홉이 전부 제외되고 ddl-drift 는
    작아진 검사 위에서 그대로 'matches the contract' 를 찍는다. 그래서 빈 목록은 답이 아니라 오류다."""
    listing = tmp_path / "declared.txt"
    listing.write_text("", encoding="utf-8")
    done = subprocess.run([str(FILTER), str(FOREIGN), str(listing)], capture_output=True, text=True)
    assert done.returncode == 1
    assert "empty declared-object list" in done.stderr
    assert done.stdout == ""
    # ddl-drift 는 그 질의를 자기 자리에서도 한 번 더 막는다 -- 실패 메시지가 질의를 가리키게.
    assert '[ -s "$work/declared" ]' in DRIFT.read_text(encoding="utf-8")


def test_the_rule_is_on_for_everything_the_checkout_declares(tmp_path: Path):
    assert survivors(entries(), tmp=tmp_path) == []


@pytest.mark.parametrize("entry", entries())
def test_declaring_one_object_ignores_that_one_and_nothing_else(entry: str, tmp_path: Path):
    assert survivors([entry], tmp=tmp_path) == [e for e in entries() if e != entry]


def test_a_bad_entry_is_rejected_rather_than_interpolated_into_sql(tmp_path: Path):
    """이름은 pg_dump 플래그와 ALTER TABLE 에 그대로 박힌다 — 평범한 식별자가 아니면 멈춘다."""
    bad = tmp_path / "foreign.txt"
    listing = tmp_path / "declared.txt"
    listing.write_text("some_table\n", encoding="utf-8")
    for entry in ("needs.a.b", "a..b", ".a", "a.", "a-b", "Retrieval_Chunk", 'x";DROP'):
        bad.write_text(f"{entry}\n", encoding="utf-8")
        done = subprocess.run([str(FILTER), str(bad), str(listing)], capture_output=True, text=True)
        assert done.returncode == 1, entry
        assert "bad entry" in done.stderr


def test_the_column_parser_sees_the_columns_this_repo_does_declare():
    """위 검사들은 '없음'만 주장할 수 있다 — 파서가 아무것도 못 읽어도 통과한다. 여기가 그 구멍을 막는다."""
    assert {"aspect", "pattern", "ruleset", "priority"} <= declared_columns()["aspect_lexicon"]
    assert "extra" in declared_columns()["labeled_set"]


def test_the_two_ddl_guards_do_not_see_it():
    """`.txt` 라서 `*.sql` glob 밖이다 — 확장자를 바꾸면 추가-전용 검사가 이 파일을 SQL 로 읽는다."""
    assert FOREIGN not in set(DDL_DIR.glob("*.sql"))


def test_ddl_drift_reads_the_file_through_the_rule():
    body = DRIFT.read_text(encoding="utf-8")
    assert "contracts/ddl/needs/foreign.txt" in body
    assert "tool/ddl-foreign-entries" in body
    # 선언 목록은 텍스트를 두 번 파싱해서가 아니라, migrate.sh 가 막 세워 둔 스키마에서 나온다.
    # pg_catalog 인 것이 중요하다 -- information_schema 는 롤이 볼 수 있는 것만 보여서, 권한이
    # 좁아지면 선언이 줄고 그만큼이 무시가 아니라 제외로 넘어간다(검사가 조용히 작아진다).
    # 주석은 information_schema 를 이름으로 부른다(왜 안 쓰는지를 적느라). 질의가 그것을 읽지
    # 않는다는 쪽을 본다.
    assert "pg_attribute" in body and "FROM information_schema" not in body
