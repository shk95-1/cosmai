"""tool/journal against a fake `gh`, so the journal rule in AGENTS.md is checked without the network.

The refusals are the point of the file. A journal is a public comment on a public repository (#15),
written by an agent mid-run, and the two things an agent has at hand are a machine path and whatever
it just read out of the environment. Both are refused before `gh` is reached at all.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNAL = REPO_ROOT / "tool" / "journal"

FAKE_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *"-X PATCH"*) cat > "$WRITTEN"; printf '{"id":1}\\n' ;;
  *"--input"*)  cat > "$WRITTEN"; printf '{"id":2}\\n' ;;
  *)            cat "$FIXTURES/comments.json" ;;
esac
"""


@pytest.fixture
def run(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    calls = tmp_path / "calls"
    written = tmp_path / "written"

    def _run(*args: str, comments: list[dict] | None = None, home: str | None = None):
        (tmp_path / "comments.json").write_text(json.dumps(comments or []), encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
            "CALLS": str(calls),
            "WRITTEN": str(written),
        }
        if home is not None:
            env["HOME"] = home
        done = subprocess.run(
            [str(JOURNAL), *args], capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, check=False
        )
        done.calls = calls.read_text(encoding="utf-8") if calls.exists() else ""  # type: ignore[attr-defined]
        done.written = json.loads(written.read_text(encoding="utf-8")) if written.exists() else None  # type: ignore[attr-defined]
        return done

    return _run


JOURNAL_COMMENT = {"id": 900, "body": "## 저널\n- 1 ok 2026-09-01T00:00Z abc1234\n"}
OTHER_COMMENT = {"id": 800, "body": "착수합니다. 워크트리는 tool-185.\n"}


def test_a_machine_path_in_the_note_is_refused_before_anything_is_written(run):
    # 레포는 공개다(#15). 홈 경로가 든 코멘트는 지워도 이력에 남는다.
    done = run("185", "2", "ok", "/ho" + "me/user1/github_prj 에서 돌렸다", comments=[JOURNAL_COMMENT])
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert "경로" in done.stderr, done.stderr
    assert done.calls == "", done.calls
    assert done.written is None


def test_a_mount_path_is_refused_too(run):
    done = run("185", "2", "ok", "/mnt/d/data 로 옮겼다", comments=[JOURNAL_COMMENT])
    assert done.returncode == 2, done.stderr
    assert done.calls == ""


def test_a_secret_shaped_token_is_refused(run):
    # 에이전트 정의가 secret 파일을 읽지 않게 막지만, 손에 든 값을 메모에 옮겨 적는 경로는 막지 않는다.
    done = run("185", "3", "fail", "키 ABCDEFGHIJKLMNOPQRSTUV 로 실패", comments=[JOURNAL_COMMENT])
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert done.calls == ""


def test_the_home_directory_is_rewritten_before_the_guard_runs(run, tmp_path: Path):
    # ~ 로 쓰면 통과해야 한다 -- 안 그러면 규칙을 지킨 메모까지 거부당하고 도구를 안 쓰게 된다.
    home = str(tmp_path / "somewhere")
    done = run("185", "2", "ok", f"{home}/cosmai 에서 돌렸다", home=home, comments=[JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "~/cosmai 에서 돌렸다" in done.written["body"], done.written
    assert home not in done.written["body"], done.written


def test_a_line_is_appended_to_the_one_journal_comment(run):
    done = run("185", "2", "ok", "재빌드 끝", comments=[OTHER_COMMENT, JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "-X PATCH" in done.calls and "issues/comments/900" in done.calls, done.calls
    body = done.written["body"]
    assert body.startswith("## 저널\n"), body
    assert "- 1 ok 2026-09-01T00:00Z abc1234" in body, body
    last = body.strip().splitlines()[-1]
    assert last.startswith("- 2 ok "), last
    assert last.endswith(" 재빌드 끝"), last
    # 시각은 UTC, SHA 는 지금 HEAD -- 어느 판 위에서 한 단계인지가 저널의 전부다.
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, check=True
    ).stdout.strip()
    assert head in last, (last, head)
    assert "Z " in last, last


def test_the_journal_comment_is_created_when_there_is_none(run):
    done = run("185", "1", "예정", comments=[OTHER_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "-X PATCH" not in done.calls, done.calls
    assert "issues/185/comments" in done.calls, done.calls
    assert done.written["body"].startswith("## 저널\n- 1 예정 "), done.written


def test_the_repo_defaults_to_this_checkout_and_r_overrides_it(run):
    assert "shk95-1/cosmai/issues/185" in run("185", "1", "예정").calls
    other = run("-R", "shk95-1/cosmai-import-ydc", "38", "1", "예정").calls
    assert "shk95-1/cosmai-import-ydc/issues/38" in other, other


def test_an_unknown_status_is_a_usage_error(run):
    done = run("185", "2", "done", comments=[JOURNAL_COMMENT])
    assert done.returncode == 64, (done.returncode, done.stderr)
    assert done.calls == ""
