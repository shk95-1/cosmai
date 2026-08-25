"""tool/issue against a fake `gh`, so the rules in #60 are checked without the network.

The fake reads the repository name out of the `-f query=` string and answers with a fixture, which
is the whole reason the query names the repository inline instead of passing it as a variable: a
test that cannot tell the two repos apart cannot exercise the cross-repo blockedBy that #55 <- fork#6
actually has.
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
for arg in "$@"; do
  case "$arg" in
    query=*'name: "cosmai-import-ydc"'*) cat "$FIXTURES/fork.json"; exit 0 ;;
    query=*'name: "cosmai"'*) cat "$FIXTURES/upstream.json"; exit 0 ;;
  esac
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

    def _run(*args: str, upstream: list[dict], fork: list[dict] | None = None, **fixture_kwargs):
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
        }
        return subprocess.run(
            [str(ISSUE), *args], capture_output=True, text=True, cwd=REPO_ROOT, env=env, check=False
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
            issue(13, "머신 경로", body=BODY + "\n/home/user1/x\n", labels=("ch:tool",), parent=10),
            issue(20, "메모인데 채널", body=MEMO_BODY, labels=("memo", "ch:tool"), parent=10),
        ],
    )
    assert done.returncode == 1
    reasons = done.stdout
    assert "cosmai#11:" in reasons and "ch:*" in reasons
    assert "cosmai#12:" in reasons and "완료 기준" in reasons
    assert "cosmai#13:" in reasons and "/home/" in reasons
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
    assert "1" in done.stdout
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
