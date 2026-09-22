"""The #288 operator surface prints counts and keeps the sample closed."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tool" / "hash-pre-rule-comment-authors"


@pytest.fixture
def command() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("hash_pre_rule_comment_authors", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _result(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def test_sample_load_proves_both_refusals_and_an_empty_reader_before_inserting(
    command: ModuleType, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []
    denied = "ERROR: permission denied for schema needs_verify\n"
    answers = iter(
        [_result(3, stderr=denied), _result(3, stderr=denied), _result(0, "0\n"), _result(0, "50\n")]
    )

    def fake(_container: str, sql: str) -> subprocess.CompletedProcess[str]:
        calls.append(sql)
        return next(answers)

    monkeypatch.setattr(command, "_psql", fake)
    assert command.sample_load("postgres") == 50
    assert "SET ROLE needs_runtime" in calls[0]
    assert "SET ROLE postgrest_anon" in calls[1]
    assert "SET ROLE needs_verify_reader" in calls[2]
    assert "SELECT DISTINCT" in calls[3]
    assert "yt-handoff-20260819" in calls[3]
    assert "LIMIT 50" in calls[3]


def test_a_broken_probe_is_not_mistaken_for_a_permission_refusal(
    command: ModuleType, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(command, "_psql", lambda *_: _result(2, stderr="container not found"))
    with pytest.raises(command.PsqlError, match="without proving a permission refusal"):
        command._must_be_refused("postgres", "needs_runtime")


def test_sample_verify_requires_hash_match_stored_hash_and_no_raw_value(
    command: ModuleType, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(command, "_psql", lambda *_: _result(0, "50,50,50,0\n"))
    assert command.sample_verify("postgres") == (50, 50, 50, 0)

    monkeypatch.setattr(command, "_psql", lambda *_: _result(0, "49,50,50,1\n"))
    with pytest.raises(command.PsqlError, match="hash=49/50 stored=50/50 raw_present=1"):
        command.sample_verify("postgres")


def test_the_sample_queries_return_counts_not_identifiers(command: ModuleType):
    # The candidate columns occur only inside INSERT/joins. The only top-level SELECTs are aggregate
    # counts, so psql cannot print a channel id even when an operator runs with -A -t.
    assert command.SAMPLE_LOAD.rstrip().endswith("SELECT count(*) FROM inserted;")
    assert command.SAMPLE_VERIFY.rstrip().endswith("FROM needs_verify.author_sample;")
    assert "SELECT channel_id FROM" not in command.SAMPLE_VERIFY
