"""어느 극성 구현이 어느 `lexicon_category` 를 소유하는가 — 이 파일 하나가 그 답이다 (#31).

005 의 자연키 `(src, ref, need_key, extractor_version, md5(sentence))` 에는 `polarity_version` 이
없다. 그래서 같은 추출기 버전으로 두 구현이 같은 문장을 처리하면 **행 단위 소유권이 성립하지 않는다** —
나중에 도는 쪽이 제자리 upsert 로 앞의 라벨을 덮고, 스코프 없는 실행의 삭제문은 남은 것마저 지운다.
소유는 그래서 행이 아니라 scope 단위다: 주인만 그 scope 를 쓰고 지우며, 주인이 없는 scope 는 지금처럼
누구든(=규칙) 갱신한다. `lexicon_category IS NULL` 인 행(댓글·카테고리 없는 리뷰)은 주인이 없다.

값은 그 구현이 산출 행에 찍는 `polarity_version` 그대로다 — 실행이 자기 것인지 아는 유일한 단서가
그 문자열이다. 판본이 오르면(few-shot·프롬프트 날짜) 이 표도 같이 옮겨야 하고,
tests/test_analyze_polarity.py 의 소유 표 검사가 그 순간을 잡는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from analysis.polarity import VERSION as RULE_VERSION

__all__ = ["NO_OWNERS", "OWNERS", "foreign_scopes", "unready"]

# 2026-08-24 홀드아웃에서 gemma4 가 규칙을 넘었다 (interfaces.md §LLM 실측). #31 이 정의한 나머지
# 26개 카테고리는 패스에 6~7시간이 들어 지금은 돌리지 않기로 했다 — 등록만 해 두고 패스를 안 돌리면
# 그 scope 는 규칙 크론이 건너뛰어(#31) 새 수집분에 라벨이 안 붙고 얼어붙으므로, 등록을 미루고 표를
# 선블록 하나로 되돌려 그동안 규칙이 나머지를 계속 갱신하게 한다. 나중에 카테고리를 꺼낼 때는 등록과
# 패스를 같은 순간에 한다(등록만 하고 미루지 않는다).
_GEMMA4_2026_08_24 = "llm-ollama-gemma4:latest-fs2-20260824"
OWNERS: Mapping[str, str] = MappingProxyType({"선블록": _GEMMA4_2026_08_24})
# 소유가 없던 시절의 동작 그대로 — 이 표를 비우면 규칙 실행이 다시 전량을 쓰고 지운다.
NO_OWNERS: Mapping[str, str] = MappingProxyType({})


def foreign_scopes(owners: Mapping[str, str], polarity_version: str) -> tuple[str, ...]:
    """이 판정자가 손대면 안 되는 scope — 다른 구현이 주인인 자리다."""
    return tuple(sorted(scope for scope, owner in owners.items() if owner != polarity_version))


def unready(owners: Mapping[str, str], version: str, scope: str | None) -> str | None:
    """규칙이 아닌 구현을 손으로 풀어도 되는 자리인가 — 아니면 그 이유 한 줄 (cosmai/cli.py 가 부른다).

    두 가지를 묻는다. 스코프를 이름 붙였는가: 안 붙인 한 줄은 규칙 모집단 전량을 다시 라벨한다(시간과
    GPU 가 든다 — 유료 여부가 기준이 아닌 이유다). 그 scope 에 이미 주인이 있는가: 주인 없는 scope 는
    규칙이 매일 05:00 에 다시 라벨하므로, 등록 없이 도는 패스는 성공하고도 다음 새벽에 사라진다.
    남의 scope 를 지정한 실행은 여기서 걸러내지 않는다 — 그 거절은 단계의 몫이고 계약은 그것을
    failed run + 종료 코드 1 로 약속한다 (contracts/entrypoints.md §분석).
    """
    if version == RULE_VERSION:
        return None
    if scope is None:
        return (
            f"--impl {version} would relabel every scope, not one; "
            "name one with --scope <category> (analysis/polarity/ownership.py)"
        )
    if scope not in owners:
        return (
            f"{scope} has no owner, so the 05:00 rule run relabels it tonight; register it to "
            f"{version} in analysis/polarity/ownership.py before this pass"
        )
    return None
