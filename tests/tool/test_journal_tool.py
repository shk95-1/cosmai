"""tool/journal against a fake `gh`, so the journal rule in AGENTS.md is checked without the network.

The refusals are the point of the file. A journal is a public comment on a public repository (#15),
written by an agent mid-run, and the two things an agent has at hand are a machine path and whatever
it just read out of the environment. Both are refused before `gh` is reached at all.

The Korean anchors the tool still accepts (#192's migration window) are read from
tests/tool/fixtures rather than written here: tool/checks/lang stops a Hangul literal from reaching
a .py file, and the follow-up that drops the anchors drops the fixture with them.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNAL = REPO_ROOT / "tool" / "journal"
KO = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "korean_anchors.json").read_text(encoding="utf-8")
)
HEAD = "## Journal"

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
  # which is the only moment a second journal comment can be seen.
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
        cwd: Path | None = None,
    ):
        (tmp_path / "comments.json").write_text(json.dumps(comments or []), encoding="utf-8")
        if relist is not None:
            (tmp_path / "list2.json").write_text(json.dumps(relist), encoding="utf-8")
        # Without `reads` the comment is not moving: every read answers with what the listing said.
        heads = (HEAD, KO["journal_head"])
        listed = [c for c in (comments or []) if c["body"].startswith(heads)]
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
            [str(JOURNAL), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd or REPO_ROOT),
            env=env,
            check=False,
        )
        done.calls = calls.read_text(encoding="utf-8") if calls.exists() else ""  # type: ignore[attr-defined]
        done.written = json.loads(written.read_text(encoding="utf-8")) if written.exists() else None  # type: ignore[attr-defined]
        return done

    return _run


JOURNAL_COMMENT = {
    "id": 900,
    "body": f"{HEAD}\n- 1 ok 2026-09-01T00:00Z abc1234\n",
    "created_at": "2026-09-01T00:00:00Z",
    "updated_at": "2026-09-01T00:00:00Z",
}


def edited(*extra: str, at: str) -> dict:
    return {
        "id": 900,
        "body": JOURNAL_COMMENT["body"] + "".join(line + "\n" for line in extra),
        "updated_at": at,
    }


OTHER_COMMENT = {"id": 800, "body": "Starting. Worktree tool-185.\n"}


def test_a_machine_path_in_the_note_is_refused_before_anything_is_written(run):
    # The repositories are public (#15) and a deleted comment keeps its edit history. This home is
    # not the session's own $HOME -- that one is rewritten to ~ and passes, so measuring the refusal
    # needs somebody else's.
    done = run("185", "2", "ok", "ran it under /ho" + "me/other/github_prj", comments=[JOURNAL_COMMENT])
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert "path" in done.stderr, done.stderr
    assert done.calls == "", done.calls
    assert done.written is None


def test_a_mount_path_is_refused_too(run):
    done = run("185", "2", "ok", "moved to /mnt/d/data", comments=[JOURNAL_COMMENT])
    assert done.returncode == 2, done.stderr
    assert done.calls == ""


def test_a_secret_shaped_token_is_refused(run):
    # The agent definition stops the secret file from being read; it does not stop a value already
    # in hand from being copied into a note.
    done = run("185", "3", "fail", "failed with key ABCDEFGHIJKLMNOPQRSTUV", comments=[JOURNAL_COMMENT])
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    assert done.calls == ""


def test_the_home_directory_is_rewritten_before_the_guard_runs(run, tmp_path: Path):
    # A note written with ~ has to pass, or the rule-abiding note is refused and the tool stops
    # being used.
    home = str(tmp_path / "somewhere")
    done = run("185", "2", "ok", f"ran it under {home}/cosmai", home=home, comments=[JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "ran it under ~/cosmai" in done.written["body"], done.written
    assert home not in done.written["body"], done.written


def test_a_line_is_appended_to_the_one_journal_comment(run):
    done = run("185", "2", "ok", "rebuild done", comments=[OTHER_COMMENT, JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "-X PATCH" in done.calls and "issues/comments/900" in done.calls, done.calls
    body = done.written["body"]
    assert body.startswith(f"{HEAD}\n"), body
    assert "- 1 ok 2026-09-01T00:00Z abc1234" in body, body
    last = body.strip().splitlines()[-1]
    assert last.startswith("- 2 ok "), last
    assert last.endswith(" rebuild done"), last
    # The time is UTC and the SHA is the current HEAD -- which revision a step happened on is the
    # whole of what a journal carries.
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, check=True
    ).stdout.strip()
    assert head in last, (last, head)
    assert "Z " in last, last


def test_the_journal_comment_is_created_when_there_is_none(run):
    done = run("185", "1", "planned", comments=[OTHER_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "-X PATCH" not in done.calls, done.calls
    assert "issues/185/comments" in done.calls, done.calls
    assert done.written["body"].startswith(f"{HEAD}\n- 1 planned "), done.written


def test_a_journal_written_in_korean_is_still_the_one_appended_to(run):
    # #192's migration window: the journals already on the issues carry the Korean head, and a tool
    # that stopped seeing them would open a second journal on every one of those issues.
    legacy = {
        "id": 900,
        "body": f"{KO['journal_head']}\n- 1 ok 2026-09-01T00:00Z abc1234\n",
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
    }
    done = run("185", "2", "ok", comments=[OTHER_COMMENT, legacy])
    assert done.returncode == 0, done.stderr
    assert "-X PATCH" in done.calls and "issues/comments/900" in done.calls, done.calls
    body = done.written["body"]
    assert body.startswith(KO["journal_head"]), body
    assert body.strip().splitlines()[-1].startswith("- 2 ok "), body


def test_the_korean_planned_state_is_accepted_and_written_in_english(run):
    # The window accepts the old word; what it writes is the new one, so the journal stops growing
    # in two languages while both are legal.
    done = run("185", "1", KO["state_planned"], comments=[OTHER_COMMENT])
    assert done.returncode == 0, done.stderr
    assert done.written["body"].startswith(f"{HEAD}\n- 1 planned "), done.written
    assert KO["state_planned"] not in done.written["body"], done.written


def _repo_with_origin(tmp_path: Path, origin: str) -> Path:
    """A throwaway git repo whose `origin` names the given URL -- `tool/journal` derives the repo
    from that remote, so a test proving it does so must never read the real checkout's own origin
    (#199, isolation pattern from test_foreign_closes.py's `gitrepo`)."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(repo)]
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        [*git[:-2], "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True, env=clean
    )
    # `tool/journal` puts `git rev-parse --short HEAD` on the journal line, so the fixture needs a commit.
    subprocess.run(
        [*git, "commit", "-q", "--allow-empty", "-m", "chore: seed"],
        check=True,
        capture_output=True,
        env=clean,
    )
    subprocess.run([*git, "remote", "add", "origin", origin], check=True, capture_output=True, env=clean)
    return repo


@pytest.mark.parametrize(
    "origin, nwo",
    [
        ("https://github.com/shk95-1/cosmai", "shk95-1/cosmai"),
        ("https://github.com/shk95/cosmai-import-ydc", "shk95/cosmai-import-ydc"),
    ],
    ids=["upstream-origin", "fork-origin"],
)
def test_the_repo_defaults_to_this_checkout_and_r_overrides_it(run, tmp_path: Path, origin: str, nwo: str):
    checkout = _repo_with_origin(tmp_path, origin)
    assert f"{nwo}/issues/185" in run("185", "1", "planned", cwd=checkout).calls
    other = run("-R", "shk95-1/cosmai-import-ydc", "38", "1", "planned", cwd=checkout).calls
    assert "shk95-1/cosmai-import-ydc/issues/38" in other, other


def test_an_unknown_status_is_a_usage_error(run):
    done = run("185", "2", "done", comments=[JOURNAL_COMMENT])
    assert done.returncode == 64, (done.returncode, done.stderr)
    assert done.calls == ""


def test_a_line_added_between_the_two_reads_is_not_lost(run):
    # Workers run concurrently and a wave epic's journal is written by two of them. Appending to the
    # GET snapshot silently drops whatever arrived in between, and the journal loses the one thing
    # it is for: how far the work got.
    other = "- 4 ok 2026-09-03T05:00Z bbb2222 their step"
    done = run(
        "185",
        "5",
        "ok",
        "my step",
        comments=[OTHER_COMMENT, JOURNAL_COMMENT],
        reads=[edited(other, at="2026-09-03T05:00:00Z"), edited(other, at="2026-09-03T05:00:00Z")],
    )
    assert done.returncode == 0, done.stderr
    body = done.written["body"]
    assert other in body, body
    assert body.strip().splitlines()[-1].endswith(" my step"), body
    # Read again and write on top of that: appending to the first snapshot would have sent a body
    # without their line in it.
    assert done.calls.count("issues/comments/900") >= 2, done.calls


def test_a_comment_that_keeps_moving_is_refused_rather_than_overwritten(run):
    # When it keeps diverging, stop rather than overwrite until you win -- somebody else's line
    # matters more than one line of journal.
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
    assert "another session" in done.stderr, done.stderr


def test_a_full_git_sha_in_the_note_is_not_a_secret(run):
    # A journal is where SHAs are written. A 40-character commit SHA caught by the secret heuristic
    # would refuse the note the rule asks for.
    sha = "0f863fa" + "0" * 33
    done = run("185", "2", "ok", f"ran on {sha}", comments=[JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert sha in done.written["body"], done.written


def test_two_first_lines_at_once_leave_one_journal_and_delete_the_duplicate(run):
    # Two workers writing a wave epic's first journal line at nearly the same moment both see "no
    # journal yet" and both create one. Left alone, the later comment keeps its single line outside
    # every subsequent call's view.
    theirs = {
        "id": 700,
        "body": f"{HEAD}\n- 1 planned 2026-09-03T06:00Z aaa1111\n",
        "created_at": "2026-09-03T06:00:01Z",
        "updated_at": "2026-09-03T06:00:01Z",
    }
    mine = {"id": 2, "body": f"{HEAD}\n- 1 planned ...\n", "created_at": "2026-09-03T06:00:02Z"}
    done = run(
        "185",
        "1",
        "planned",
        comments=[OTHER_COMMENT],
        # Mine comes first in the listing -- the canonical one has to be the earlier created_at.
        relist=[OTHER_COMMENT, mine, theirs],
        reads=[theirs],
    )
    assert done.returncode == 0, done.stderr
    assert "issues/comments/700" in done.calls, done.calls
    body = done.written["body"]
    assert "- 1 planned 2026-09-03T06:00Z aaa1111" in body, body
    assert body.strip().splitlines()[-1].startswith("- 1 planned "), body
    assert len(body.strip().splitlines()) == 3, body
    # The duplicate is mine, so I am the one who deletes it.
    assert "-X DELETE" in done.calls and "issues/comments/2" in done.calls, done.calls
    assert "issues/comments/700" in [line for line in done.calls.splitlines() if "-X PATCH" in line][0]


def test_the_journal_that_was_there_first_is_the_one_written_to(run):
    # The rule is created_at, not listing order. Leaning on listing order starts appending to a
    # different comment the day a page boundary or a sort changes.
    late = {
        "id": 901,
        "body": f"{HEAD}\n- 9 ok 2026-09-02T00:00Z ddd4444\n",
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
    }
    done = run("185", "2", "ok", comments=[late, JOURNAL_COMMENT], reads=[JOURNAL_COMMENT])
    assert done.returncode == 0, done.stderr
    assert "issues/comments/900" in done.calls, done.calls
    assert "issues/comments/901" not in done.calls, done.calls
