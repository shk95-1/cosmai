"""AGENTS.md is auto-loaded into every session and subagent, so it must stay a pointer page.

Twenty-five lines of body was the ceiling agreed in issue #60: enough for the boot order and the
absolute rules, not enough to become a second STATE.md.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CEILING = 25


def _body_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def test_agents_md_stays_a_pointer_page():
    lines = _body_lines((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert len(lines) <= CEILING, f"AGENTS.md has {len(lines)} body lines; the ceiling is {CEILING}"


def test_agents_md_names_the_boot_order_and_the_rules_index():
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for needle in ("tool/issue audit", "tool/issue ready", "STATE.md", "[규약]", "blockedBy"):
        assert needle in text, needle


def test_claude_md_only_imports_agents_md():
    # One file for every model: CLAUDE.md is Claude Code's import hook and nothing else.
    assert (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
