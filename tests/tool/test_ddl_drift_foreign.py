"""contracts/ddl/needs/foreign.txt: the list declaring foreign DDL that landed in production needs (#75).

Only tool/checks/ddl-drift reads this file, but that check demands the production DB and docker, so
it cannot run in the suite. What runs here is the promise the file keeps — its format, and "nothing
of ours is among the excluded" (excluding one of ours hides that object's drift forever).

The sentence of that promise differs between upstream and the fork (upstream cosmai#108). The same
list is all 'foreign' in upstream, while in the fork cosmai-import-ydc 020~025 and db/views/ declare
those nine themselves, so they are all 'ours'. So the verdict is asked of the **checkout** rather
than of the list: an object this checkout declares is ignored in the list (= not excluded, compared
as it is), and only the rest are excluded. Ignoring does not weaken the check — an ignored object
stands in the expectation and stays in the production dump, so it is still compared. One thing
implements that rule, tool/ddl-foreign-entries, and the tests below inject a declaration list to ask
back that the rule turns on and off.
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
    """The same read as ddl-drift's `sed 's/#.*//' | awk 'NF {print $1}'`."""
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
    """A view is a declaration of this checkout too — db/migrate.sh rebuilds db/views/*.sql on every
    deploy, and pg_dump's -T catches views as well, so a listed view is excluded just like a table."""
    views: set[str] = set()
    for path in sorted(VIEW_DIR.glob("*.sql")):
        views |= set(re.findall(r"CREATE VIEW needs\.(\w+)", path.read_text(encoding="utf-8")))
    return views


def declared_columns() -> dict[str, set[str]]:
    """Columns in the CREATE TABLE body + ADD COLUMN from later migrations. Constraint lines such as
    PRIMARY KEY·UNIQUE start with an uppercase letter, so the lowercase-identifier regex below never
    picks them up in the first place."""
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
    """Builds from the file the same list ddl-drift asks of a throwaway container's pg_catalog.
    When the two reads diverge, test_the_survivors_are_exactly_what_this_checkout_does_not_declare
    below cries."""
    objects = declared_tables() | declared_views()
    for table, columns in declared_columns().items():
        objects |= {f"{table}.{column}" for column in columns}
    return objects


def kinds(declared: Iterable[str], tmp: Path) -> list[str]:
    """Runs tool/ddl-foreign-entries with an injected declaration list and hands back the lines
    ddl-drift receives, unchanged."""
    listing = tmp / "declared.txt"
    listing.write_text("".join(f"{name}\n" for name in sorted(declared)), encoding="utf-8")
    out = subprocess.run(
        [str(FILTER), str(FOREIGN), str(listing)], capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def survivors(declared: Iterable[str], tmp: Path) -> list[str]:
    """Only the names of the entries that will be excluded — an ignored one is not here."""
    return [line.split(" ", 1)[1] for line in kinds(declared, tmp)]


def test_the_file_states_the_convention_it_exists_for():
    body = FOREIGN.read_text(encoding="utf-8")
    assert "남의 DDL 이 운영에 들어오면 선언한다" in body
    # The head of the file has to carry why the same list means opposite things in two repos (cosmai#108).
    assert "이 체크아웃이 선언하는 객체는 무시한다" in body


def test_every_entry_is_a_table_or_a_table_column():
    assert entries(), "an empty list means this file is not needed — delete it"
    assert [e for e in entries() if not ENTRY.match(e)] == []


def test_the_first_entry_is_the_forks_retrieval_chunk():
    assert entries()[0] == "retrieval_chunk"


def test_nothing_this_checkout_declares_is_ever_excluded(tmp_path: Path):
    """Exactly the value the original guard kept — if one of our objects drops out of the production
    dump, its drift is never visible again. The answer differs in the two repos while the sentence is
    the same: upstream declares none of the nine, so all nine are excluded; the fork declares all nine,
    so none is."""
    assert set(survivors(declared_objects(), tmp=tmp_path)) & declared_objects() == set()


def test_the_survivors_are_exactly_what_this_checkout_does_not_declare(tmp_path: Path):
    """Whether the sh rule and the python read above give the same answer — it cries when comment
    stripping or the table/column split diverges."""
    declared = declared_objects()
    assert survivors(declared, tmp=tmp_path) == [e for e in entries() if e not in declared]


def upstream_shaped() -> set[str]:
    """The shape of an upstream checkout's declaration set — 001~008 are declared and none of these
    nine is. Down to `aspect_lexicon` staying while only `aspect_lexicon.extra` drops out, as there
    (021 is the fork's)."""
    listed = set(entries())
    return {o for o in declared_objects() if o not in listed and o.split(".")[0] not in listed}


def test_an_upstream_checkout_declares_none_of_them_so_the_rule_is_off(tmp_path: Path):
    """The rule turns on when 'this checkout declares it', not when 'the list is empty'.
    Given an upstream-shaped declaration set (not empty), all nine survive and the exclusion list
    ddl-drift builds has the same entries in the same order as before this rule existed."""
    declared = upstream_shaped()
    assert declared, "faking upstream with an empty list makes the assertions below pass for another reason"
    assert "aspect_lexicon" in declared and "aspect_lexicon.extra" not in declared
    assert survivors(declared, tmp=tmp_path) == entries()
    assert kinds(declared, tmp=tmp_path)[:2] == [
        "table retrieval_chunk",
        "column aspect_lexicon.extra",
    ]


def test_an_empty_declaration_list_stops_the_check_instead_of_quietly_widening_it(tmp_path: Path):
    """This rule fails silently in one direction: when the declaration list is empty all nine are
    excluded and ddl-drift stamps 'matches the contract' on a check that shrank. So an empty list is
    an error, not an answer."""
    listing = tmp_path / "declared.txt"
    listing.write_text("", encoding="utf-8")
    done = subprocess.run([str(FILTER), str(FOREIGN), str(listing)], capture_output=True, text=True)
    assert done.returncode == 1
    assert "empty declared-object list" in done.stderr
    assert done.stdout == ""
    # ddl-drift blocks that query once more in its own place -- so the failure message points at it.
    assert '[ -s "$work/declared" ]' in DRIFT.read_text(encoding="utf-8")


def test_the_rule_is_on_for_everything_the_checkout_declares(tmp_path: Path):
    assert survivors(entries(), tmp=tmp_path) == []


@pytest.mark.parametrize("entry", entries())
def test_declaring_one_object_ignores_that_one_and_nothing_else(entry: str, tmp_path: Path):
    assert survivors([entry], tmp=tmp_path) == [e for e in entries() if e != entry]


def test_a_bad_entry_is_rejected_rather_than_interpolated_into_sql(tmp_path: Path):
    """The name is baked into pg_dump flags and ALTER TABLE as it is — anything but a plain
    identifier stops."""
    bad = tmp_path / "foreign.txt"
    listing = tmp_path / "declared.txt"
    listing.write_text("some_table\n", encoding="utf-8")
    for entry in ("needs.a.b", "a..b", ".a", "a.", "a-b", "Retrieval_Chunk", 'x";DROP'):
        bad.write_text(f"{entry}\n", encoding="utf-8")
        done = subprocess.run([str(FILTER), str(bad), str(listing)], capture_output=True, text=True)
        assert done.returncode == 1, entry
        assert "bad entry" in done.stderr


def test_the_column_parser_sees_the_columns_this_repo_does_declare():
    """The checks above can only claim 'absence' — they pass even when the parser reads nothing.
    This is where that hole is closed."""
    assert {"aspect", "pattern", "ruleset", "priority"} <= declared_columns()["aspect_lexicon"]
    assert "extra" in declared_columns()["labeled_set"]


def test_the_two_ddl_guards_do_not_see_it():
    """`.txt`, so it is outside the `*.sql` glob — change the extension and the additive-only check
    reads this file as SQL."""
    assert FOREIGN not in set(DDL_DIR.glob("*.sql"))


def test_ddl_drift_reads_the_file_through_the_rule():
    body = DRIFT.read_text(encoding="utf-8")
    assert "contracts/ddl/needs/foreign.txt" in body
    assert "tool/ddl-foreign-entries" in body
    # The declaration list comes from the schema migrate.sh has just built, not from parsing the text twice.
    # pg_catalog matters -- information_schema shows only what the role may see, so narrower grants
    # shrink the declarations and that much moves from ignored to excluded (the check quietly shrinks).
    # The comment names information_schema (while writing down why it is not used). This looks at the
    # side where the query does not read it.
    assert "pg_attribute" in body and "FROM information_schema" not in body
