"""The way to stamp the dictionary revision again (`tool/show-lexicon-stamp`, fork #62).

the place that confirms again the dictionary version the contract (`contracts/interfaces.md`
§Retrieval measurements) wrote down -- the same as what `tool/show-vector-stamp` does on the vector axis, and
the string it emits **has to be** the one `retrieval eval` puts on every row. With two copies, the version the
contract quoted parts from the version the rows record.
"""

from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from analysis.retrieval import topics
from tests.retrieval.conftest import install_topics

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tool" / "show-lexicon-stamp"


def loaded() -> ModuleType:
    """It has no extension, so a plain import does not reach it (the same way as
    `test_vector_floor.loaded`)."""
    spec = spec_from_loader("show_lexicon_stamp", SourceFileLoader("show_lexicon_stamp", str(TOOL)))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tool_is_linted_like_the_rest():
    """ruff does not look at a file with no extension by default -- without adding it, this tool alone
    lives outside the checks (fork #61)."""
    assert TOOL.exists()
    assert f'"tool/{TOOL.name}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_csv_stamp_is_the_string_the_dictionary_itself_gives(capsys):
    """If the tool builds the revision string separately, the value the contract quotes and the value on the
    evaluation rows drift apart quietly."""
    assert loaded().main(["--csv", str(topics.DICTIONARY_CSV)]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("ruleset=retrieval-topic · version=미적재")
    assert printed.endswith(f"fingerprint={_csv_dictionary().fingerprint}")


def _csv_dictionary() -> topics.Topics:
    from tests.retrieval.conftest import csv_topics

    return csv_topics(version=None)


def test_an_unreadable_source_is_blocked_not_failed(capsys, tmp_path):
    # A dictionary not there yet is not a failure but work not done -- the same place as a missing vector
    # store.
    assert loaded().main(["--csv", str(tmp_path / "없다.csv")]) == 2
    assert loaded().main(["--csv", str(topics.DICTIONARY_CSV), "--version", "3"]) == 2
    capsys.readouterr()


@pytest.mark.postgres
def test_the_stamp_of_the_active_version_comes_from_the_database(needs_runtime_url: str, capsys):
    from db.seed._common import connect

    with connect(needs_runtime_url) as conn:
        install_topics(conn, version=1)
        stamped = topics.load(conn).stamp
    assert loaded().main(["--url", needs_runtime_url]) == 0
    assert capsys.readouterr().out.strip() == stamped
    assert "version=1" in stamped


@pytest.mark.postgres
def test_two_versions_are_compared_on_the_axis_they_differ_on(needs_runtime_url: str, capsys):
    """`cosmai lexicon diff` is per row, so an added alias in one topic looks like one row and a changed
    order is not visible at all. Retracing the revision of the six lines is the comparison on this axis
    (fork #62)."""
    from db.lexicon import activate, insert_aspects
    from db.seed._common import connect
    from tests.retrieval.conftest import csv_rows

    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        insert_aspects(cur, csv_rows(), 1, active=False)
        wider = ("백탁", "generic", "", "허옇", False, topics.RULESET, 1, {"term_kind": "ko"})
        insert_aspects(cur, [*csv_rows(), wider], 2, active=False)
        activate(cur, "aspect", 2)
        conn.commit()
    assert loaded().main(["--version", "2", "--against", "1", "--url", needs_runtime_url]) == 0
    out = capsys.readouterr().out
    assert "version=2" in out and "version=1" in out
    assert "~ 백탁.ko" in out
    # A difference is an answer, not a blocker -- the exit code does not move on it.
    assert loaded().main(["--version", "1", "--against", "1", "--url", needs_runtime_url]) == 0
    assert "차이 없다" in capsys.readouterr().out
