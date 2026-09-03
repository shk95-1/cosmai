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
# when-touched 의 완료 시점은 이 이슈가 아니라 그 파일을 만지는 다른 작업에 얹혀 있다 --
# 그래서 완료 기준 대신 「언제 고치나」를 쓴다 (#137).
WHEN_TOUCHED_BODY = "## 사실\n지금은 안 터진다\n\n## 언제 고치나\n그 파일을 만지는 작업이 함께 고친다\n"


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


def closed(number: int, title: str, body: str, *, days_ago: float, labels: tuple[str, ...]) -> dict:
    row = issue(number, title, body=body, labels=labels, updated_days_ago=days_ago)
    row["closedAt"] = stamp(days_ago)
    return row


def released(date: str) -> str:
    return BODY + f"\n## 해제 조건\n{date} 이후\n"


def day(days_from_now: float) -> str:
    return (NOW + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


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


def partial_page(repo: str, issues: list[dict], labels: list[str] | None = None) -> dict:
    """The same page after the nested fields timed out: arrays present, contents gone.

    This is what makes the failure quiet. `issues.nodes` is still a list of issues, so the
    repository looks answered; it is `subIssues`, `assignees` and `blockedBy` that came back
    empty, and those are exactly what the queue order, the held-resource summary and the blockers
    are read from. The server says so in `errors` -- the only place the loss is visible.
    """
    hollowed = []
    for source in issues:
        item = dict(source)
        item["assignees"] = {"nodes": []}
        item["subIssues"] = {"nodes": []}
        item["blockedBy"] = {"pageInfo": {"hasNextPage": False}, "nodes": []}
        hollowed.append(item)
    answer = page(repo, hollowed, labels)
    answer["errors"] = [
        {"message": "Something went wrong while executing your query.", "type": "SERVICE_UNAVAILABLE"}
    ]
    return answer


FAKE_GH = """#!/bin/sh
# The fork pattern is tested first because "cosmai" is a substring of "cosmai-import-ydc":
# the looser case would answer for both repos and the cross-repo tests would prove nothing.
for arg in "$@"; do
  case "$arg" in
    query=*'name: "cosmai-import-ydc"'*) which=fork ;;
    query=*'name: "cosmai"'*) which=upstream ;;
    *) continue ;;
  esac
  # recheck (e) needs closed issues, which the shared graph does not carry; the fixture is
  # separate so a test can prove the closed page is fetched by recheck and by nothing else.
  case "$arg" in *'states: CLOSED'*) cat "$FIXTURES/$which.closed.json"; exit 0 ;; esac
  if [ "$FAKE_GH_FAIL" = "$which" ]; then echo "fake gh: the API said no" >&2; exit 1; fi
  if [ "$FAKE_GH_ERRORS" = "$which" ]; then
    echo '{"errors":[{"message":"Although you appear to have the correct authorization"}]}'
    exit 0
  fi
  # A partial failure: `data` arrives, but the expensive nested fields timed out and the
  # server said so in `errors`. The nodes are still arrays, so a guard that only asks
  # "did data arrive" reads this as the truth.
  if [ "$FAKE_GH_PARTIAL" = "$which" ]; then
    cat "$FIXTURES/$which.partial.json"
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
        (tmp_path / "upstream.partial.json").write_text(
            json.dumps(partial_page(UPSTREAM, upstream, fixture_kwargs.get("upstream_labels"))),
            encoding="utf-8",
        )
        (tmp_path / "fork.partial.json").write_text(
            json.dumps(partial_page(FORK, fork or [], fixture_kwargs.get("fork_labels"))),
            encoding="utf-8",
        )
        (tmp_path / "upstream.closed.json").write_text(
            json.dumps(page(UPSTREAM, fixture_kwargs.get("upstream_closed") or [])), encoding="utf-8"
        )
        (tmp_path / "fork.closed.json").write_text(
            json.dumps(page(FORK, fixture_kwargs.get("fork_closed") or [])), encoding="utf-8"
        )
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
            "COSMAI_ISSUE_REPOS": f"{UPSTREAM} {FORK}",
            "FAKE_GH_FAIL": fixture_kwargs.get("gh_fails_on", ""),
            "FAKE_GH_ERRORS": fixture_kwargs.get("gh_errors_on", ""),
            "FAKE_GH_PARTIAL": fixture_kwargs.get("gh_partial_on", ""),
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


def test_ready_leads_with_the_resources_the_running_issues_hold(run):
    # The cap on workers is gone (#185): what a new issue collides with is a resource someone is
    # already holding, and that is only visible if the first line says who holds what.
    resourced = "## 채널·자리 / 등급 / 규모\n규모 M · 자원: ops(구 스택 정지 = 매번 승인) · 공유DB\n"
    done = run(
        "ready",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "진행 중", body=resourced, labels=("ch:tool",), assignees=("shk95",)),
        ],
        fork=[
            issue(6, "포크 진행 중", body=resourced, labels=("ch:analysis/retrieval",), assignees=("shk95",))
        ],
    )
    assert done.returncode == 0, done.stderr
    first = done.stdout.splitlines()[0]
    assert first.startswith("진행 중 2 · 점유:"), done.stdout
    # One row per resource with both repos on it: the collision that matters is the cross-repo one.
    assert "ops cosmai#11, cosmai-import-ydc#6" in first, first
    assert "공유DB cosmai#11, cosmai-import-ydc#6" in first, first
    assert "WIP" not in done.stdout and "금지" not in done.stdout, done.stdout
    assert "in progress: shk95 since" in done.stdout


def test_a_resource_of_none_is_not_folded_into_the_held_summary(run):
    # 자원: 없음 is most issues. Folding it would put a row on the first line that blocks nothing.
    done = run(
        "ready",
        "--json",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "원장", labels=("ch:repo",), assignees=("shk95",)),
        ],
    )
    assert done.returncode == 0, done.stderr
    model = json.loads(done.stdout)
    assert model["held"] == {"in_progress": 1, "resources": []}, model["held"]
    # The gate is deleted, not hidden behind a flag: nothing may read wip/limit/gate again.
    assert "wip" not in model and "limit" not in model and "gate" not in model, list(model)


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


def test_a_partial_response_is_not_read_as_the_whole_graph(run):
    # HTTP 200 with `data` AND `errors`: the nodes are arrays, so "did data arrive" says yes.
    # What is missing is the nesting, and the queue is built out of the nesting.
    done = run(
        "audit",
        upstream=[epic(10, "tool", subs=(11,)), issue(11, "일", labels=("ch:tool",), parent=10)],
        gh_partial_on="upstream",
    )
    assert done.returncode != 0, done.stdout
    assert done.stdout.strip() == "", done.stdout
    assert UPSTREAM in done.stderr, done.stderr


def test_a_partial_response_does_not_empty_the_held_summary(run):
    # The sharpest loss: `assignees` comes back empty, so two issues someone is already working
    # read as startable and the resources they hold vanish. Dying is the only safe answer.
    done = run(
        "ready",
        upstream=[
            epic(10, "tool", subs=(11, 12)),
            issue(11, "하나", labels=("ch:tool",), parent=10, assignees=("shk95",)),
            issue(12, "둘", labels=("ch:tool",), parent=10, assignees=("shk95",)),
        ],
        gh_partial_on="upstream",
    )
    assert done.returncode != 0, done.stdout
    assert "진행 중" not in done.stdout, done.stdout


def test_a_partial_response_on_the_fork_names_the_fork(run):
    # Two repos share one graph; the message has to say which half was lost.
    done = run(
        "lint",
        upstream=[epic(10, "tool", subs=(11,)), issue(11, "일", labels=("ch:tool",), parent=10)],
        fork=[epic(20, "population", subs=(21,)), issue(21, "포크", labels=("ch:population",), parent=20)],
        gh_partial_on="fork",
    )
    assert done.returncode != 0, done.stdout
    assert FORK in done.stderr, done.stderr


def test_a_when_touched_issue_needs_no_completion_criteria(run):
    # 완료 시점이 다른 작업에 얹혀 있는 부류다. 완료 기준을 요구하면 라벨의 뜻과 어긋나고,
    # 실제로 그 어긋남이 lint 를 계속 빨갛게 두었다(포크 #43·#44).
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "만질 때", body=WHEN_TOUCHED_BODY, labels=("ch:tool", "when-touched"), parent=10),
        ],
    )
    assert done.returncode == 0, done.stdout
    assert done.stdout.strip() == "", done.stdout


def test_a_when_touched_issue_without_when_to_fix_is_a_lint_error(run):
    # 면제가 곧 무규칙은 아니다 -- 언제 고치는지는 여전히 적혀 있어야 한다.
    done = run(
        "lint",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "만질 때", body=BODY, labels=("ch:tool", "when-touched"), parent=10),
        ],
    )
    assert done.returncode != 0, done.stdout
    assert "언제 고치나" in done.stdout, done.stdout


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


def test_recheck_names_each_reason_with_the_checklist_items_to_walk(run):
    # AGENTS.md's recheck rule is five questions (전제 · blockedBy · 해제 조건 · 등급 · 중복). A bare
    # list of issue numbers would leave the reader to guess which of the five this row is about.
    quoted = BODY + "\n`tool/issue` 는 있고 `tool/nowhere.py:12` 는 없다\n"
    done = run(
        "recheck",
        "--json",
        upstream=[
            epic(10, "tool", subs=(11, 12, 13, 14, 15)),
            issue(11, "묵음", labels=("ch:tool",), parent=10, updated_days_ago=20),
            issue(12, "날짜 지남", body=released(day(-2)), labels=("ch:tool", "deferred"), parent=10),
            issue(
                13,
                "산문 조건",
                body=BODY + "\n## 해제 조건\n소비자가 생기면\n",
                labels=("ch:tool", "deferred"),
                parent=10,
            ),
            issue(14, "옮겨간 경로", body=quoted, labels=("ch:tool",), parent=10),
            issue(15, "목표가 닫혔다", labels=("ch:tool",), parent=10),
        ],
        upstream_closed=[
            closed(30, "[목표] 끝난 목표", "#15 로 이어진다\n", days_ago=2, labels=("goal",)),
            closed(31, "[결정] 오래된 결정", "#11 로 이어진다\n", days_ago=30, labels=("decision",)),
        ],
    )
    assert done.returncode == 1, done.stdout + done.stderr
    by_key = {row["key"]: row for row in json.loads(done.stdout)}
    assert set(by_key) == {f"cosmai#{n}" for n in (11, 12, 13, 14, 15)}, sorted(by_key)
    assert {r["code"] for r in by_key["cosmai#11"]["reasons"]} == {"a"}
    assert by_key["cosmai#11"]["reasons"][0]["checks"] == ["전제", "blockedBy", "해제 조건", "등급", "중복"]
    assert {r["code"] for r in by_key["cosmai#12"]["reasons"]} == {"b"}
    assert by_key["cosmai#12"]["reasons"][0]["checks"] == ["해제 조건"]
    assert day(-2) in by_key["cosmai#12"]["reasons"][0]["why"]
    assert {r["code"] for r in by_key["cosmai#13"]["reasons"]} == {"c"}
    assert by_key["cosmai#13"]["reasons"][0]["checks"] == ["blockedBy", "해제 조건"]
    assert {r["code"] for r in by_key["cosmai#14"]["reasons"]} == {"d"}
    assert "tool/nowhere.py" in by_key["cosmai#14"]["reasons"][0]["why"]
    # The path that is there must not be reported, or the reason becomes noise nobody reads.
    assert "tool/issue" not in by_key["cosmai#14"]["reasons"][0]["why"]
    assert {r["code"] for r in by_key["cosmai#15"]["reasons"]} == {"e"}
    assert "cosmai#30" in by_key["cosmai#15"]["reasons"][0]["why"]
    # 30일 전에 닫힌 결정은 이미 반영됐다고 본다 -- 안 그러면 목록이 영원히 자란다.
    assert not any(r["code"] == "e" for r in by_key["cosmai#11"]["reasons"])


def test_recheck_renders_the_reason_and_the_checklist(run):
    done = run(
        "recheck",
        upstream=[
            epic(10, "tool", subs=(11,)),
            issue(11, "날짜 지남", body=released(day(-2)), labels=("ch:tool", "deferred"), parent=10),
        ],
    )
    assert done.returncode == 1, done.stdout
    assert "재점검 1건" in done.stdout, done.stdout
    rows = [line for line in done.stdout.splitlines() if line.startswith("    ")]
    assert rows == ["    해제 조건 " + day(-2) + " 이 지났다 → 점검: 해제 조건"], done.stdout
    assert "  cosmai#11 · 날짜 지남" in done.stdout, done.stdout


def test_recheck_leaves_alone_what_is_still_waiting_for_its_condition(run):
    # #86 (date not yet reached) and #183 (deferred behind an open blocker) are the two shapes that
    # must stay quiet, or boot starts with a list of issues nobody can act on.
    done = run(
        "recheck",
        upstream=[
            epic(10, "tool", subs=(11, 12)),
            issue(11, "아직 이르다", body=released(day(5)), labels=("ch:tool", "deferred"), parent=10),
            issue(
                12,
                "막혀 있다",
                body=BODY + "\n## 해제 조건\n네이버가 끝난 뒤\n",
                labels=("ch:tool", "deferred"),
                parent=10,
                blocked_by=((UPSTREAM, 11, "OPEN"),),
            ),
        ],
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "재점검 없음" in done.stdout, done.stdout
    assert "cosmai#11" not in done.stdout and "cosmai#12" not in done.stdout, done.stdout


def test_recheck_does_not_read_a_repo_name_or_a_label_as_a_path(run):
    # Bodies are full of `owner/repo` and `ch:collectors/youtube`. Reported as missing files they
    # would put every issue on the list, which is the same as having no list.
    body = BODY + "\n`shk95-1/cosmai` 와 `ch:collectors/youtube` 와 `tool/checks/paths`\n"
    done = run(
        "recheck",
        upstream=[epic(10, "tool", subs=(11,)), issue(11, "인용", body=body, labels=("ch:tool",), parent=10)],
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "cosmai#11" not in done.stdout, done.stdout


def test_recheck_skips_memos(run):
    # A memo has its own 14-day track in audit and the user queue; listing it twice teaches the
    # reader that recheck is mostly memos.
    done = run(
        "recheck",
        upstream=[issue(20, "묵은 메모", body=MEMO_BODY, labels=("memo",), updated_days_ago=40)],
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "cosmai#20" not in done.stdout, done.stdout


def test_recheck_reads_the_last_date_in_the_release_section_not_any_date(run):
    # #86 은 해제 조건 절 안에 지난 날짜와 미래 날짜를 함께 적는다("재구성 2026-08-25 + 14일; 2026-09-08
    # 이후"). 아무 날짜나 지났으면 잡는 규칙이면 아직 이른 이슈가 매 부팅 목록에 오르고, #18 은 반대로
    # 과거 날짜 둘이라 잡혀야 한다. 가장 늦은 날짜만이 두 경우를 다 맞힌다.
    mixed = "## 해제 조건\n컷오버(%s) 후 7일간 문제가 없고 %s 이후\n"
    done = run(
        "recheck",
        "--json",
        upstream=[
            epic(10, "tool", subs=(11, 12)),
            issue(11, "아직 이르다", body=BODY + mixed % (day(-9), day(5)), labels=("ch:tool",), parent=10),
            issue(12, "지났다", body=BODY + mixed % (day(-9), day(-2)), labels=("ch:tool",), parent=10),
        ],
    )
    assert done.returncode == 1, done.stdout + done.stderr
    rows = json.loads(done.stdout)
    assert [row["key"] for row in rows] == ["cosmai#12"], rows
    assert [r["code"] for r in rows[0]["reasons"]] == ["b"], rows
    assert day(-2) in rows[0]["reasons"][0]["why"], rows
    assert day(-9) not in rows[0]["reasons"][0]["why"], rows
