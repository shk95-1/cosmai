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
  *"-X PATCH"*)  cat > "$WRITTEN"; printf '{"id":1}\\n' ;;
  *"-X DELETE"*) printf '{}\\n' ;;
  *"--input"*)   cat > "$WRITTEN"; printf '{"id":2,"created_at":"2026-09-03T06:00:02Z"}\\n' ;;
  # One comment by id. Every read answers with the next fixture, which is how a comment that
  # somebody else is editing at the same moment looks from here.
  *"issues/comments/"*)
      n=$(cat "$SEQ" 2>/dev/null || echo 0)
      n=$((n + 1)); printf '%s' "$n" > "$SEQ"
      [ -f "$FIXTURES/read$n.json" ] && cat "$FIXTURES/read$n.json" || cat "$FIXTURES/read-last.json" ;;
  # The comment listing, once per call: the second one is what the issue looks like after a POST,
  # which is the only moment a second `## 저널` comment can be seen.
  *)
      n=$(cat "$LSEQ" 2>/dev/null || echo 0)
      n=$((n + 1)); printf '%s' "$n" > "$LSEQ"
      [ -f "$FIXTURES/list$n.json" ] && cat "$FIXTURES/list$n.json" || cat "$FIXTURES/comments.json" ;;
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

    def _run(
        *args: str,
        comments: list[dict] | None = None,
        home: str | None = None,
        reads: list[dict] | None = None,
        relist: list[dict] | None = None,
    ):
        (tmp_path / "comments.json").write_text(json.dumps(comments or []), encoding="utf-8")
        if relist is not None:
            (tmp_path / "list2.json").write_text(json.dumps(relist), encoding="utf-8")
        # Without `reads` the comment is not moving: every read answers with what the listing said.
        listed = [c for c in (comments or []) if c["body"].startswith("## 저널")]
        for index, row in enumerate(reads or listed, start=1):
            (tmp_path / f"read{index}.json").write_text(json.dumps(row), encoding="utf-8")
        (tmp_path / "read-last.json").write_text(
            json.dumps((reads or listed or [{"id": 0, "body": ""}])[-1]), encoding="utf-8"
        )
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
            "CALLS": str(calls),
            "SEQ": str(tmp_path / "seq"),
            "LSEQ": str(tmp_path / "lseq"),
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


JOURNAL_COMMENT = {
    "id": 900,
    "body": "## 저널\n- 1 ok 2026-09-01T00:00Z abc1234\n",
    "created_at": "2026-09-01T00:00:00Z",
    "updated_at": "2026-09-01T00:00:00Z",
}


def edited(*extra: str, at: str) -> dict:
    return {
        "id": 900,
        "body": JOURNAL_COMMENT["body"] + "".join(line + "\n" for line in extra),
        "updated_at": at,
    }


OTHER_COMMENT = {"id": 800, "body": "착수합니다. 워크트리는 tool-185.\n"}


def test_a_machine_path_in_the_note_is_refused_before_anything_is_written(run):
    # 레포는 공개다(#15). 홈 경로가 든 코멘트는 지워도 이력에 남는다. 이 홈은 이 세션의 $HOME 이
    # 아니다 -- 자기 홈은 ~ 로 바뀌어 통과하므로, 거부를 재려면 남의 홈이어야 한다.
    done = run("185", "2", "ok", "/ho" + "me/other/github_prj 에서 돌렸다", comments=[JOURNAL_COMMENT])
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


def test_a_line_added_between_the_two_reads_is_not_lost(run):
    # 워커는 동시에 여럿이고 wave 채널 에픽의 저널은 그중 둘이 같이 쓴다. GET 스냅숏에 이어붙여 PATCH 하면
    # 그 사이에 들어온 남의 줄이 조용히 사라지고, 저널은 "어디까지 갔는지" 를 잃는다 -- 도구의 목적 자체다.
    other = "- 4 ok 2026-09-03T05:00Z bbb2222 남의 단계"
    done = run(
        "185",
        "5",
        "ok",
        "내 단계",
        comments=[OTHER_COMMENT, JOURNAL_COMMENT],
        reads=[edited(other, at="2026-09-03T05:00:00Z"), edited(other, at="2026-09-03T05:00:00Z")],
    )
    assert done.returncode == 0, done.stderr
    body = done.written["body"]
    assert other in body, body
    assert body.strip().splitlines()[-1].endswith(" 내 단계"), body
    # 다시 읽고 그 위에 얹는다: 첫 스냅숏에 이어붙였다면 남의 줄이 없는 본문이 올라갔을 것이다.
    assert done.calls.count("issues/comments/900") >= 2, done.calls


def test_a_comment_that_keeps_moving_is_refused_rather_than_overwritten(run):
    # 계속 갈리면 이길 때까지 덮어쓰는 대신 그만둔다 -- 저널 한 줄보다 남의 줄이 더 중요하다.
    done = run(
        "185",
        "5",
        "ok",
        comments=[OTHER_COMMENT, JOURNAL_COMMENT],
        reads=[
            edited("- 4 ok 2026-09-03T05:00Z bbb2222", at="2026-09-03T05:00:00Z"),
            edited(
                "- 4 ok 2026-09-03T05:00Z bbb2222",
                "- 5 ok 2026-09-03T05:01Z ccc3333",
                at="2026-09-03T05:01:00Z",
            ),
            edited("- 6 ok 2026-09-03T05:02Z ddd4444", at="2026-09-03T05:02:00Z"),
            edited("- 7 ok 2026-09-03T05:03Z eee5555", at="2026-09-03T05:03:00Z"),
        ],
    )
    assert done.returncode == 3, (done.returncode, done.stdout, done.stderr)
    assert done.written is None, done.written
    assert "동시" in done.stderr or "바뀌" in done.stderr, done.stderr


def test_a_full_git_sha_in_the_note_is_not_a_secret(run):
    # 저널은 SHA 를 적는 곳이다. 40자 커밋 SHA 가 secret 휴리스틱에 걸리면 규칙대로 쓴 메모가 거부된다.
    sha = "0f863fa" + "0" * 33
    done = run("185", "2", "ok", f"{sha} 위에서 돌렸다", comments=[JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert sha in done.written["body"], done.written


def test_two_first_lines_at_once_leave_one_journal_and_delete_the_duplicate(run):
    # 두 워커가 wave 에픽의 저널 첫 줄을 거의 동시에 쓰면 둘 다 "저널 없음" 을 보고 각자 코멘트를 만든다.
    # 그대로 두면 뒤진 코멘트는 한 줄만 가진 채 도구 시야 밖에 영원히 남는다.
    theirs = {
        "id": 700,
        "body": "## 저널\n- 1 예정 2026-09-03T06:00Z aaa1111\n",
        "created_at": "2026-09-03T06:00:01Z",
        "updated_at": "2026-09-03T06:00:01Z",
    }
    mine = {"id": 2, "body": "## 저널\n- 1 예정 …\n", "created_at": "2026-09-03T06:00:02Z"}
    done = run(
        "185",
        "1",
        "예정",
        comments=[OTHER_COMMENT],
        # 목록 순서로는 내 것이 먼저다 -- 정본은 created_at 이 이른 쪽이어야 한다.
        relist=[OTHER_COMMENT, mine, theirs],
        reads=[theirs],
    )
    assert done.returncode == 0, done.stderr
    assert "issues/comments/700" in done.calls, done.calls
    body = done.written["body"]
    assert "- 1 예정 2026-09-03T06:00Z aaa1111" in body, body
    assert body.strip().splitlines()[-1].startswith("- 1 예정 "), body
    assert len(body.strip().splitlines()) == 3, body
    # 중복은 내가 만든 것이므로 내가 지운다.
    assert "-X DELETE" in done.calls and "issues/comments/2" in done.calls, done.calls
    assert "issues/comments/700" in [line for line in done.calls.splitlines() if "-X PATCH" in line][0]


def test_the_journal_that_was_there_first_is_the_one_written_to(run):
    # 선택 규칙은 목록 순서가 아니라 created_at 이다. 목록 순서에 기대면 페이지 경계나 정렬이 바뀌는 날
    # 다른 코멘트에 붙기 시작한다.
    late = {
        "id": 901,
        "body": "## 저널\n- 9 ok 2026-09-02T00:00Z ddd4444\n",
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }
    done = run("185", "2", "ok", comments=[late, JOURNAL_COMMENT], reads=[JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "issues/comments/900" in done.calls, done.calls
    assert "issues/comments/901" not in done.calls, done.calls
