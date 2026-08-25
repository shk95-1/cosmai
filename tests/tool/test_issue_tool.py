"""tool/issue against a fake `gh`, so the rules in #60 are checked without the network.

The fake reads the repository name out of the `-f query=` string and answers with a fixture, which
is the whole reason the query names the repository inline instead of passing it as a variable: a
test that cannot tell the two repos apart cannot exercise the cross-repo blockedBy that #55 <- fork#6
actually has.

Isolation here is PATH precedence, not conftest's socket block: tool/issue shells out, and a
subprocess is outside the guard that stops in-process sockets.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ISSUE = REPO_ROOT / "tool" / "issue"
UPSTREAM = "slopindustries/cosmai"
FORK = "slopindustries/cosmai-import-ydc"
COMMON_LABELS = ["channel", "goal", "decision", "memo", "when-touched", "needs-user"]

NOW = datetime.now(UTC)
BODY = "## 맥락\n뭔가\n\n## 완료 기준\n기계로 검사된다\n\n## 채널·자리 / 등급 / 규모\n규모 S · 자원: 없음\n"
MEMO_BODY = "## 맥락\n관찰만 적는다\n"


def stamp(days_ago: float = 0.0) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(
    number: int,
    title: str = "제목",
    *,
    body: str = BODY,
    labels: tuple[str, ...] = (),
    assignees: tuple[str, ...] = (),
    parent: int | None = None,
    subs: tuple[int, ...] = (),
    blocked_by: tuple[tuple[str, int, str], ...] = (),
    updated_days_ago: float = 0.0,
) -> dict:
    return {
        "number": number,
        "title": title,
        "updatedAt": stamp(updated_days_ago),
        "body": body,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "assignees": {"nodes": [{"login": login} for login in assignees]},
        "parent": None if parent is None else {"number": parent},
        "subIssues": {"nodes": [{"number": n} for n in subs]},
        "blockedBy": {
            "nodes": [
                {"number": n, "state": state, "repository": {"nameWithOwner": repo}}
                for repo, n, state in blocked_by
            ]
        },
    }


def page(repo: str, issues: list[dict], labels: list[str] | None = None) -> dict:
    return {
        "data": {
            "repository": {
                "nameWithOwner": repo,
                "labels": {"nodes": [{"name": n} for n in (COMMON_LABELS if labels is None else labels)]},
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": issues,
                },
            }
        }
    }


FAKE_GH = """#!/bin/sh
# The fork pattern is tested first because "cosmai" is a substring of "cosmai-import-ydc":
# the looser case would answer for both repos and the cross-repo tests would prove nothing.
for arg in "$@"; do
  case "$arg" in
    query=*'name: "cosmai-import-ydc"'*) which=fork ;;
    query=*'name: "cosmai"'*) which=upstream ;;
    *) continue ;;
  esac
  if [ "$FAKE_GH_FAIL" = "$which" ]; then echo "fake gh: the API said no" >&2; exit 1; fi
  if [ "$FAKE_GH_ERRORS" = "$which" ]; then
    echo '{"errors":[{"message":"Although you appear to have the correct authorization"}]}'
    exit 0
  fi
  cat "$FIXTURES/$which.json"
  exit 0
done
echo "fake gh: no fixture for: $*" >&2
exit 1
"""


@pytest.fixture
def run(tmp_path: Path):
    """Runs tool/issue with a fake `gh` first on PATH and the fixtures it should answer with."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)

    def _run(
        *args: str,
        upstream: list[dict],
        fork: list[dict] | None = None,
        cwd: Path | None = None,
        **fixture_kwargs,
    ):
        (tmp_path / "upstream.json").write_text(
            json.dumps(page(UPSTREAM, upstream, fixture_kwargs.get("upstream_labels"))), encoding="utf-8"
        )
        (tmp_path / "fork.json").write_text(
            json.dumps(page(FORK, fork or [], fixture_kwargs.get("fork_labels"))), encoding="utf-8"
        )
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
            "COSMAI_ISSUE_REPOS": f"{UPSTREAM} {FORK}",
            "FAKE_GH_FAIL": fixture_kwargs.get("gh_fails_on", ""),
            "FAKE_GH_ERRORS": fixture_kwargs.get("gh_errors_on", ""),
        }
        return subprocess.run(
            [str(ISSUE), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd or REPO_ROOT),
            env=env,
            check=False,
        )

    return _run


def epic(number: int, channel: str, subs: tuple[int, ...] = ()) -> dict:
    return issue(number, f"[{channel}] 에픽", labels=("channel", f"ch:{channel}"), subs=subs)


def test_a_closed_blocker_does_not_hold_an_issue_back(run):
    # blockedBy keeps closed rows, so a tool that reads presence instead of state reports the whole
    # backlog as blocked the moment anything upstream of it is finished.
    done = run(
        "ready",
        "--json",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "닫힌 블로커", labels=("ch:tool",), blocked_by=((UPSTREAM, 9, "CLOSED"),)),
        ],
    )
    assert done.returncode == 0, done.stderr
    item = json.loads(done.stdout)["channels"][0]["items"][0]
    assert item["status"] == "ready", item


def test_a_blocker_in_the_other_repo_blocks(run):
    # cosmai#55 <- cosmai-import-ydc#6 is live today; one repo at a time would call #55 ready.
    done = run(
        "ready",
        "--json",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "교차 레포", labels=("ch:tool",), blocked_by=((FORK, 6, "OPEN"),)),
        ],
        fork=[issue(6, "포크 쪽 일", labels=("ch:analysis/retrieval",))],
    )
    assert done.returncode == 0, done.stderr
    item = json.loads(done.stdout)["channels"][0]["items"][0]
    assert item["status"] == "blocked"
    assert item["detail"] == "cosmai-import-ydc#6"
    assert (
        "blocked: cosmai-import-ydc#6"
        in run(
            "ready",
            upstream=[
                epic(10, "tool", subs=(11,)),
                issue(11, "교차 레포", labels=("ch:tool",), blocked_by=((FORK, 6, "OPEN"),)),
            ],
            fork=[issue(6, "포크 쪽 일", labels=("ch:analysis/retrieval",))],
        ).stdout
    )


def test_two_assignees_across_the_repos_close_the_gate(run):
    # The cap is two workers over both repos, so counting one repo at a time would wave a third in.
    done = run(
        "ready",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "진행 중", labels=("ch:tool",), assignees=("shk95",)),
        ],
        fork=[issue(6, "포크 진행 중", labels=("ch:analysis/retrieval",), assignees=("shk95",))],
    )
    assert done.returncode == 0, done.stderr
    assert "WIP 2/2" in done.stdout
    assert "새 착수 금지" in done.stdout
    assert "in progress: shk95 since" in done.stdout


def test_the_coordinators_repo_issue_does_not_occupy_a_worker_slot(run):
    # The coordinator claims its own ledger issue (ch:repo) while dispatching workers; counting it
    # closed the gate on a fresh session in the #60 cold-boot test.
    done = run(
        "ready",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "워커", labels=("ch:tool",), assignees=("shk95",)),
            epic(20, "repo", subs=(21,)),
            issue(21, "원장", labels=("ch:repo",), assignees=("shk95",)),
        ],
    )
    assert done.returncode == 0, done.stderr
    assert "WIP 1/2" in done.stdout
    assert "새 착수 금지" not in done.stdout


def test_a_channel_issue_without_a_parent_is_reported_at_the_end_of_its_channel(run):
    done = run(
        "ready",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "제자리", labels=("ch:tool",)),
            issue(12, "떠돌이", labels=("ch:tool",)),
        ],
    )
    assert done.returncode == 0, done.stderr
    lines = [line for line in done.stdout.splitlines() if "cosmai#1" in line]
    assert "(부모 없음)" in lines[-1] and "cosmai#12" in lines[-1], done.stdout
    assert "(부모 없음)" not in lines[0]


def test_being_blocked_by_a_memo_is_a_lint_error(run):
    # A memo has no completion criterion, so an issue blocked by one can never become ready.
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "메모에 막힘", labels=("ch:tool",), parent=10, blocked_by=((UPSTREAM, 20, "OPEN"),)),
            issue(20, "관찰", body=MEMO_BODY, labels=("memo",)),
        ],
    )
    assert done.returncode == 1
    lines = done.stdout.splitlines()
    assert any(line.startswith("cosmai#11:") and "memo" in line for line in lines), done.stdout


def test_a_blockedby_cycle_is_found_across_the_repos(run):
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "가", labels=("ch:tool",), parent=10, blocked_by=((FORK, 6, "OPEN"),)),
        ],
        fork=[
            epic(5, "analysis/retrieval", subs=(6,)),
            issue(
                6,
                "나",
                labels=("ch:analysis/retrieval",),
                parent=5,
                blocked_by=((UPSTREAM, 11, "OPEN"),),
            ),
        ],
    )
    assert done.returncode == 1
    cycles = [line for line in done.stdout.splitlines() if "순환" in line]
    assert len(cycles) == 1, done.stdout
    assert "cosmai#11" in cycles[0] and "cosmai-import-ydc#6" in cycles[0]


def test_a_deferred_issue_with_a_release_condition_section_passes(run):
    # #60 allows either shape, so a deferred issue whose condition is an observation and not an
    # issue must not be reported -- otherwise `lint` is noise and stops being run.
    body = BODY + "\n## 해제 조건\n검색 소비자가 생기면\n"
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "미룸", body=body, labels=("ch:tool", "deferred"), parent=10),
        ],
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == ""


def test_a_deferred_issue_with_neither_condition_is_a_lint_error(run):
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "미룸", labels=("ch:tool", "deferred"), parent=10),
        ],
    )
    assert done.returncode == 1
    assert "해제 조건" in done.stdout


def test_lint_reports_the_rest_of_the_registration_rules(run):
    done = run(
        "lint",
        upstream=[
            issue(11, "채널 둘", labels=("ch:tool", "ch:repo"), parent=10),
            issue(12, "완료 기준 없음", body="## 맥락\n없다\n", labels=("ch:tool",), parent=10),
            issue(13, "머신 경로", body=BODY + "\n/ho" + "me/user1/x\n", labels=("ch:tool",), parent=10),
            issue(20, "메모인데 채널", body=MEMO_BODY, labels=("memo", "ch:tool"), parent=10),
        ],
    )
    assert done.returncode == 1
    reasons = done.stdout
    assert "cosmai#11:" in reasons and "ch:*" in reasons
    assert "cosmai#12:" in reasons and "완료 기준" in reasons
    assert "cosmai#13:" in reasons and "머신 경로" in reasons
    assert "cosmai#20:" in reasons


def test_the_user_queue_is_ordered_by_how_much_it_unblocks(run):
    done = run(
        "ready",
        "--user",
        "--json",
        upstream=[
            epic(10, "tool", subs=(11, 12)),
            issue(11, "결정 대기", labels=("ch:tool", "needs-user"), parent=10),
            issue(12, "다른 결정", labels=("ch:tool", "needs-user"), parent=10),
            issue(13, "가", labels=("ch:tool",), parent=10, blocked_by=((UPSTREAM, 12, "OPEN"),)),
            issue(14, "나", labels=("ch:tool",), parent=10, blocked_by=((UPSTREAM, 12, "OPEN"),)),
            issue(20, "묵은 메모", body=MEMO_BODY, labels=("memo",), updated_days_ago=20),
            issue(21, "새 메모", body=MEMO_BODY, labels=("memo",), updated_days_ago=1),
        ],
    )
    assert done.returncode == 0, done.stderr
    queue = json.loads(done.stdout)["user_queue"]
    assert [row["key"] for row in queue] == ["cosmai#12", "cosmai#11", "cosmai#20"], queue
    assert queue[0]["unblocks"] == 2


def test_needs_user_is_out_of_the_default_ready_listing(run):
    fixture = [
        epic(10, "tool", subs=(11,)),
        issue(11, "결정 대기", labels=("ch:tool", "needs-user"), parent=10),
    ]
    assert "cosmai#11" not in run("ready", upstream=fixture).stdout
    assert "cosmai#11" in run("ready", "--user", upstream=fixture).stdout


def test_audit_reports_drift_without_failing(run):
    done = run(
        "audit",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "방치", labels=("ch:tool",), parent=10, assignees=("shk95",), updated_days_ago=3),
            issue(20, "묵은 메모", body=MEMO_BODY, labels=("memo",), updated_days_ago=30),
        ],
        fork_labels=["channel"],
    )
    assert done.returncode == 0, done.stderr
    assert "cosmai#11" in done.stdout
    assert "14일 넘은 memo (1건)" in done.stdout, done.stdout
    # The fork fixture is missing five of the six shared labels, which is what makes a rule
    # unenforceable in one repo while reading as enforced in the other.
    assert "when-touched" in done.stdout


def test_the_tool_says_it_is_unverified_when_gh_is_missing(tmp_path: Path):
    # Exit 69 is prerequisite's "unknown". Reporting a missing gh as a rule violation would teach
    # people that lint's exit 1 means nothing.
    empty = tmp_path / "empty"
    empty.mkdir()
    done = subprocess.run(
        [str(ISSUE), "ready"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": str(empty)},
        check=False,
    )
    assert done.returncode == 69, (done.returncode, done.stdout, done.stderr)
    assert "gh" in done.stderr


def test_the_resource_is_read_from_the_scale_section_only(run):
    # #61's own body quotes the word 자원 while describing this rule; a whole-body search printed
    # that sentence as the issue's resource.
    body = "## 할 일\n각 줄에 `자원:` 값을 붙인다\n\n## 채널·자리 / 등급 / 규모\n규모 M · 자원: 공유DB 읽기\n"
    done = run(
        "ready",
        "--json",
        upstream=[epic(10, "tool", subs=(11,)), issue(11, "규모 절", body=body, labels=("ch:tool",))],
    )
    assert json.loads(done.stdout)["channels"][0]["items"][0]["resource"] == "공유DB 읽기"


def test_a_rule_quoting_home_is_not_a_machine_path(run):
    # #60 and #61 both write the guarded prefix inside backticks to state the guard itself. Flagging that
    # makes lint cry wolf on the two issues that define the rule.
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(
                11,
                "규칙 인용",
                body=BODY + "\n- 본문에 `/ho" + "me/` 이 있다.\n",
                labels=("ch:tool",),
                parent=10,
            ),
        ],
    )
    assert done.returncode == 0, done.stdout


def test_audit_counts_the_default_ready_queue_and_needs_user_apart(run):
    # #60 Phase 4.1 wants the two to agree. The needs-user issue is the only thing that can make
    # them disagree, because it is the one issue the default listing drops.
    fixture = [
        epic(10, "tool", subs=(11, 13)),
        issue(11, "제자리", labels=("ch:tool",), parent=10),
        issue(13, "결정 대기", labels=("ch:tool", "needs-user"), parent=10),
        issue(12, "떠돌이", labels=("ch:tool",)),
    ]
    audit = run("audit", upstream=fixture).stdout
    assert "ch:tool (cosmai#10) · 2건" in audit, audit
    assert "needs-user 1건" in audit, audit
    ready = json.loads(run("ready", "--json", upstream=fixture).stdout)["channels"][0]["items"]
    assert [row["key"] for row in ready] == ["cosmai#11", "cosmai#12"]


def test_the_user_listing_marks_which_items_are_waiting_on_the_user(run):
    # --user folds two lists into one; without a marker the reader cannot tell which rows are
    # startable work and which are sitting in their own queue.
    fixture = [
        epic(10, "tool", subs=(11, 12)),
        issue(11, "결정 대기", labels=("ch:tool", "needs-user"), parent=10),
        issue(12, "그냥 일", labels=("ch:tool",), parent=10),
    ]
    rows = json.loads(run("ready", "--user", "--json", upstream=fixture).stdout)["channels"][0]["items"]
    assert [row["needs_user"] for row in rows] == [True, False]
    assert [row["status"] for row in rows] == ["ready", "ready"]
    text = run("ready", "--user", upstream=fixture).stdout
    assert "needs-user" in [line for line in text.splitlines() if "cosmai#11" in line][0]
    assert "needs-user" not in [line for line in text.splitlines() if "cosmai#12" in line][0]


def test_a_closed_memo_blocker_does_not_keep_lint_red(run):
    # Promotion closes the memo and re-issues it. A rule that reads the edge without its state
    # would keep the promoted issue red forever, which is how a check stops being run.
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "승격 뒤", labels=("ch:tool",), parent=10, blocked_by=((UPSTREAM, 20, "CLOSED"),)),
            issue(20, "닫힌 메모", body=MEMO_BODY, labels=("memo",)),
        ],
    )
    assert done.returncode == 0, done.stdout


def test_a_repo_that_fails_to_fetch_stops_the_command(run):
    # A half graph is worse than no graph: the fork holds the blocker for cosmai#55, so a swallowed
    # fork fetch would report a blocked issue as ready.
    done = run(
        "ready",
        upstream=[epic(10, "tool", subs=(11,)), issue(11, "일", labels=("ch:tool",), parent=10)],
        gh_fails_on="fork",
    )
    assert done.returncode != 0, done.stdout
    assert done.stdout.strip() == "", done.stdout
    assert FORK in done.stderr, done.stderr


def test_an_errors_response_is_not_read_as_an_empty_repo(run):
    # GraphQL answers a partial failure with HTTP 200 and no `data`, which reads as "this repo has
    # no open issues" to anything that only checks gh's exit code.
    done = run(
        "lint",
        upstream=[epic(10, "tool", subs=(11,)), issue(11, "일", labels=("ch:tool",), parent=10)],
        gh_errors_on="upstream",
    )
    assert done.returncode != 0, done.stdout
    assert done.stdout.strip() == ""
    assert UPSTREAM in done.stderr, done.stderr


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A repo whose main and working tree disagree about markers in both directions.

    git grep skips untracked files, so the working tree has to differ in tracked ones: main's
    marker is edited away, and a marker main never had is added.
    """
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(repo)]
    # Hooks export GIT_DIR; without stripping it these commands would act on the enclosing checkout.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        [*git[:-2], "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True, env=clean
    )
    (repo / "kept.py").write_text("# TO" + "DO(#7) 만지는 김에 고친다\n", encoding="utf-8")
    (repo / "scratch.py").write_text("# 표식 없음\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True, env=clean)
    subprocess.run([*git, "commit", "-qm", "chore: seed"], check=True, capture_output=True, env=clean)
    (repo / "kept.py").write_text("# 작업 중에 지운 표식\n", encoding="utf-8")
    (repo / "scratch.py").write_text("# TO" + "DO(#8) 커밋되지 않은 표식\n", encoding="utf-8")
    return repo


def test_the_todo_survey_reads_main_not_the_working_tree(run, checkout: Path):
    # Worktrees share the ref, so whichever checkout runs audit sees a different working tree and a
    # different answer. main is the one tree every worker agrees on.
    done = run(
        "audit",
        upstream=[
            epic(10, "tool", subs=(7, 9)),
            issue(7, "표식 있음", labels=("ch:tool", "when-touched"), parent=10),
            issue(9, "표식 없음", labels=("ch:tool", "when-touched"), parent=10),
        ],
        cwd=checkout,
    )
    assert done.returncode == 0, done.stderr
    assert "TO" + "DO(#8)" not in done.stdout, done.stdout
    missing = done.stdout.split("열린 when-touched 이슈인데 코드에 표식이 없다")[1]
    assert "cosmai#9" in missing and "cosmai#7" not in missing, done.stdout
