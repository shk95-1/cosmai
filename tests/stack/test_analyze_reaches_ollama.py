"""The container -> host ollama wiring (#32).

The `analyze` container had no such address, so there was no way to add the cron line that runs gemma4
either. The address is a knob, not a secret -- the value lives in `stack/env.example` ->
`stack/.env`, and `contracts/secrets.md` leaves it off its own key list (being on that list would trip
test_stack_wiring's secret check the moment a value got written there).

What this file also holds onto is the `<<:` trap: the moment `analyze` declares its own
`environment:`, the anchor's five keys are not inherited, they are **deleted**. test_stack_wiring.py
checks those five per service.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.stack.test_stack_wiring import SERVICES

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / "stack" / "env.example"
SECRETS_MD = REPO_ROOT / "contracts" / "secrets.md"
KNOB = "OLLAMA_URL"
SERVICE = "analyze"
# The address the coordinator actually measured round-tripping inside a container (2026-08-25, the
# Windows Tailscale WSL2's mirroring inherits).
DEFAULT_HOST = "http://100.102.193.98:11434"


def _knob_line() -> str | None:
    match = re.search(rf"^\s*{KNOB}:\s*(\S.*)$", SERVICES[SERVICE], re.M)
    return match.group(1).strip() if match else None


def test_the_analyze_service_is_given_the_ollama_address_through_a_knob():
    line = _knob_line()
    assert line, f"{SERVICE} 서비스에 {KNOB} 이 없다 — 컨테이너 안의 기본값은 localhost:11434 다"
    assert line.startswith(f"${{{KNOB}"), (
        f"{SERVICE} 의 {KNOB} 이 리터럴이다({line!r}) — 주소는 stack/.env 로 갈아 끼우는 노브여야 한다"
    )
    assert DEFAULT_HOST in line, f"{KNOB} 의 기본값이 실측한 주소가 아니다: {line!r}"


def test_env_example_documents_the_knob():
    assert f"{KNOB}=" in ENV_EXAMPLE.read_text(encoding="utf-8"), (
        f"stack/env.example 이 {KNOB} 을 말하지 않는다"
    )


def test_the_secret_contract_does_not_claim_this_knob_as_a_key():
    """A key `contracts/secrets.md` names in backticks may have no value attached anywhere in this repo
    (test_stack_wiring.test_no_secret_key_is_given_a_value_in_the_repo). The address must have a
    value."""
    assert f"`{KNOB}`" not in SECRETS_MD.read_text(encoding="utf-8"), (
        f"{KNOB} 은 secret 이 아니라 노브다 — 키 목록에 남기면 기본값을 적을 자리가 사라진다"
    )
