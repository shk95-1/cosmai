"""컨테이너 → 호스트 ollama 배선 (#32).

`analyze` 컨테이너에는 이 주소가 없었고, 그래서 gemma4 를 돌리는 크론 줄도 넣을 수 없었다. 주소는
secret 이 아니라 노브다 — 값은 `stack/env.example` → `stack/.env` 에 있고 `contracts/secrets.md` 는
그것을 자기 키 목록에서 뺀다(그 목록에 있으면 값을 적는 순간 test_stack_wiring 의 secret 검사에 걸린다).

이 파일이 붙드는 것은 `<<:` 의 함정이기도 하다: `analyze` 가 자기 `environment:` 를 선언하는 순간
앵커의 다섯 키는 상속이 아니라 **삭제**다. 그 다섯은 test_stack_wiring.py 가 서비스마다 검사한다.
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
# 조정자가 컨테이너 안에서 실제로 왕복시킨 주소 (2026-08-25, WSL2 미러링이 물려받은 Windows Tailscale).
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
    """`contracts/secrets.md` 가 백틱으로 이름 붙인 키에는 이 레포 어디에도 값이 붙을 수 없다
    (test_stack_wiring.test_no_secret_key_is_given_a_value_in_the_repo). 주소는 값이 있어야 한다."""
    assert f"`{KNOB}`" not in SECRETS_MD.read_text(encoding="utf-8"), (
        f"{KNOB} 은 secret 이 아니라 노브다 — 키 목록에 남기면 기본값을 적을 자리가 사라진다"
    )
