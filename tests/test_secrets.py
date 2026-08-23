"""db/secrets.py names missing keys and never carries a value into a message."""

from __future__ import annotations

from pathlib import Path

import pytest

from db import secrets


def write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "env"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_reads_key_value_lines_and_ignores_comments(tmp_path: Path):
    path = write_env(tmp_path, "# comment\n\nNEEDS_DB_RUNTIME=pw-one\nOTHER='pw two'\nnot a pair\n")
    assert secrets.load(path) == {"NEEDS_DB_RUNTIME": "pw-one", "OTHER": "pw two"}


def test_load_of_a_missing_file_is_empty(tmp_path: Path):
    assert secrets.load(tmp_path / "nope") == {}


def test_the_secret_file_can_be_pointed_at_by_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_env(tmp_path, "NEEDS_DB_RUNTIME=pw-one\n")
    monkeypatch.setenv(secrets.ENV_PATH_VAR, str(path))
    assert secrets.load() == {"NEEDS_DB_RUNTIME": "pw-one"}


def test_require_returns_the_wanted_keys(tmp_path: Path):
    path = write_env(tmp_path, "NEEDS_DB_RUNTIME=pw-one\nSPARE=x\n")
    assert secrets.require(["NEEDS_DB_RUNTIME"], path) == {"NEEDS_DB_RUNTIME": "pw-one"}


def test_require_exits_naming_only_the_missing_keys(tmp_path: Path):
    path = write_env(tmp_path, "NEEDS_DB_RUNTIME=pw-one\nEMPTY=\n")
    with pytest.raises(SystemExit) as excinfo:
        secrets.require(["NEEDS_DB_RUNTIME", "EMPTY", "ABSENT"], path)
    message = str(excinfo.value)
    assert "EMPTY" in message and "ABSENT" in message
    assert "pw-one" not in message and "NEEDS_DB_RUNTIME" not in message
